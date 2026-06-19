"""Port of src/modes/edit_message.cc (Phase 2).

A compose window combining:
  * a header grid (From dropdown + To/Cc/Bcc/Subject entries),
  * a switches strip (signature/markdown/encrypt/sign),
  * an embedded ThreadView(edit_mode=True) preview rebuilt each time the
    user saves the tmpfile,
  * a status revealer (send delay countdown / errors).

Keys mirror the C++ exactly. The encrypt/sign keys remain available but
crypto is wired in Phase 3 — for now toggling them just flips state.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qsl

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gio, Gtk  # noqa: E402

from ..actions import AddDraftMessage, AddSentMessage, RemoveMessage
from ..compose import Attachment, ComposeMessage
from ..compose_id import generate_message_id
from ..log import log
from ..models.message_thread import Message, MessageThread
from ..utils.misc import safe_fname
from .editor.external import ExternalEditor
from .mode import Mode
from .raw_message import RawMessage


HEADER_RE = re.compile(r"^([A-Za-z\-]+):\s?(.*)$")


# -- small data class for "active" mid -> originating msg map ----------------

class _UnprocessedMessage(Message):
    """Build a Message from raw bytes in memory (compose preview)."""
    def __init__(self, raw: bytes, mid: str = "preview@compose"):
        super().__init__(mid=mid)
        self.in_notmuch = False
        self.missing_content = False
        self.mid = mid
        from io import BytesIO
        import gi as _gi
        _gi.require_version("GMime", "3.0")
        from gi.repository import GMime
        GMime.init()
        stream = GMime.StreamMem.new_with_buffer(raw)
        parser = GMime.Parser.new_with_stream(stream)
        gm = parser.construct_message(None)
        if gm is None:
            self.missing_content = True
            return
        self._gm = gm
        # populate the simple Message fields
        self.subject = gm.get_subject() or ""
        frm = gm.get_from()
        from gi.repository import GMime as _GM
        self.sender = (frm.to_string(_GM.FormatOptions.get_default(), False)
                       if frm and frm.length() > 0 else "")
        self.mid = gm.get_message_id() or mid

        for atyp, attr in ((_GM.AddressType.TO, "_to"),
                           (_GM.AddressType.CC, "_cc"),
                           (_GM.AddressType.BCC, "_bcc")):
            al = gm.get_addresses(atyp)
            setattr(self, attr,
                    al.to_string(_GM.FormatOptions.get_default(), False)
                    if al and al.length() > 0 else "")

        dt = gm.get_date()
        self.time = int(dt.to_unix()) if dt else 0

        from ..models.chunk import Chunk
        part = gm.get_mime_part()
        self.root = Chunk(part, self.preferred_type) if part else None


# -- the mode ----------------------------------------------------------------

class EditMessage(Mode):
    def __init__(self, main_window, *, to: str = "", cc: str = "",
                 bcc: str = "", subject: str = "", body: str = "",
                 references: str = "", inreplyto: str = "",
                 draft_msg: Message | None = None,
                 reply_source_mid: str = "",
                 forward_source_mid: str = ""):
        super().__init__(main_window)
        self.app = main_window.app

        # account / compose object
        accts = self.app.accounts.accounts
        if not accts:
            log.error("em: no accounts configured!")
            raise RuntimeError("no accounts configured")
        self.account = self.app.accounts.default_account or accts[0]

        self.draft_msg = draft_msg
        self.reply_source_mid = reply_source_mid
        self.forward_source_mid = forward_source_mid
        self.draft_saved = False
        self.message_sent = False
        self.sending = False
        self.editor_active = False

        self.compose = ComposeMessage(self.app.config, self.account)
        self.compose.set_to(to)
        self.compose.set_cc(cc)
        self.compose.set_bcc(bcc)
        self.compose.set_subject(subject)
        self.compose.set_references(references)
        self.compose.set_inreplyto(inreplyto)
        self.compose.body = body
        self.msg_id = self.compose.id

        # tmpfile under runtime_dir
        runtime = self.app.config.std_paths.runtime_dir
        runtime.mkdir(parents=True, exist_ok=True)
        self.tmpfile_path: Path = runtime / safe_fname(self.msg_id)
        # write the initial tmpfile (headers + blank line + body)
        self._write_initial_tmpfile()

        self.set_label("New message" + (f": {subject}" if subject else ""))

        # ---- header grid ----
        grid = Gtk.Grid(column_spacing=6, row_spacing=4)
        grid.set_margin_top(6)
        grid.set_margin_bottom(6)
        grid.set_margin_start(6)
        grid.set_margin_end(6)
        self.append(grid)

        self._from_drop = Gtk.DropDown.new_from_strings(
            [a.full_address() for a in accts])
        self._from_drop.set_selected(accts.index(self.account))
        self._from_drop.connect("notify::selected", self._on_from_changed)
        grid.attach(Gtk.Label(label="From:", xalign=1), 0, 0, 1, 1)
        grid.attach(self._from_drop, 1, 0, 3, 1)

        self._to_entry = Gtk.Entry(hexpand=True, text=to)
        grid.attach(Gtk.Label(label="To:", xalign=1), 0, 1, 1, 1)
        grid.attach(self._to_entry, 1, 1, 3, 1)

        self._cc_entry = Gtk.Entry(hexpand=True, text=cc)
        grid.attach(Gtk.Label(label="Cc:", xalign=1), 0, 2, 1, 1)
        grid.attach(self._cc_entry, 1, 2, 3, 1)

        self._bcc_entry = Gtk.Entry(hexpand=True, text=bcc)
        grid.attach(Gtk.Label(label="Bcc:", xalign=1), 0, 3, 1, 1)
        grid.attach(self._bcc_entry, 1, 3, 3, 1)

        self._subject_entry = Gtk.Entry(hexpand=True, text=subject)
        grid.attach(Gtk.Label(label="Subject:", xalign=1), 0, 4, 1, 1)
        grid.attach(self._subject_entry, 1, 4, 3, 1)

        # ---- switches strip ----
        switches = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        switches.set_margin_start(6)
        self._switch_signature = Gtk.Switch(
            active=self.compose.include_signature)
        self._switch_markdown = Gtk.Switch(active=self.compose.markdown)
        self._switch_encrypt = Gtk.Switch(active=self.compose.encrypt)
        self._switch_sign = Gtk.Switch(active=self.compose.sign)
        for label, sw in (("Signature", self._switch_signature),
                          ("Markdown", self._switch_markdown),
                          ("Encrypt", self._switch_encrypt),
                          ("Sign", self._switch_sign)):
            b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            b.append(Gtk.Label(label=label))
            b.append(sw)
            switches.append(b)
        self.append(switches)

        # ---- status revealer ----
        self._status_revealer = Gtk.Revealer()
        self._status_label = Gtk.Label(xalign=0)
        self._status_label.set_margin_start(6)
        self._status_revealer.set_child(self._status_label)
        self.append(self._status_revealer)

        # ---- preview pane ----
        from .thread_view.thread_view import ThreadView
        self.thread_view = ThreadView(main_window, edit_mode=True)
        self.thread_view.set_vexpand(True)
        self.append(self.thread_view)

        # ---- wire compose signals ----
        self.compose.connect("message-send-status", self._on_send_status)
        self.compose.connect("message-sent", self._on_message_sent)

        # ---- keys ----
        self._register_keys()

        # initial preview
        GLib.idle_add(self._rebuild_preview)

    # -- tmpfile / preview ---------------------------------------------------

    def _write_initial_tmpfile(self) -> None:
        # the C++ external editor stores both headers and body in the tmpfile
        # so the user can edit From/To/Subject directly in their editor.
        text = []
        text.append(f"From: {self.account.full_address()}")
        text.append(f"To: {self.compose.to}")
        text.append(f"Cc: {self.compose.cc}")
        text.append(f"Bcc: {self.compose.bcc}")
        text.append(f"Subject: {self.compose.subject}")
        if self.compose.references:
            text.append(f"References: {self.compose.references}")
        if self.compose.inreplyto:
            text.append(f"In-Reply-To: {self.compose.inreplyto}")
        text.append("")
        text.append(self.compose.body)
        self.tmpfile_path.write_text("\n".join(text), encoding="utf-8")

    def _read_edited(self) -> None:
        """Parse the tmpfile back into compose fields + body."""
        try:
            raw = self.tmpfile_path.read_text(encoding="utf-8")
        except OSError as e:
            log.error("em: could not read tmpfile: %s", e)
            return

        headers, _sep, body = raw.partition("\n\n")
        for line in headers.splitlines():
            m = HEADER_RE.match(line)
            if not m:
                continue
            key, val = m.group(1).strip().lower(), m.group(2).strip()
            if key == "from":
                # try to pick the matching account
                for i, a in enumerate(self.app.accounts.accounts):
                    if a.email and a.email in val:
                        self.account = a
                        self._from_drop.set_selected(i)
                        self.compose.set_from(a)
                        break
            elif key == "to":
                self.compose.set_to(val)
                self._to_entry.set_text(val)
            elif key == "cc":
                self.compose.set_cc(val)
                self._cc_entry.set_text(val)
            elif key == "bcc":
                self.compose.set_bcc(val)
                self._bcc_entry.set_text(val)
            elif key == "subject":
                self.compose.set_subject(val)
                self._subject_entry.set_text(val)
            elif key == "references":
                self.compose.set_references(val)
            elif key == "in-reply-to":
                self.compose.set_inreplyto(val.strip("<>"))

        self.compose.body = body
        self._rebuild_preview()

    def _rebuild_preview(self) -> bool:
        try:
            self._sync_compose_from_ui()
            self.compose.build()
            self.compose.finalize()
            raw = self.compose.to_bytes()
        except Exception as e:
            log.error("em: rebuild preview failed: %s", e)
            return False

        mt = MessageThread()
        mt.messages = [_UnprocessedMessage(raw, mid=self.msg_id)]
        self.thread_view.load_message_thread(mt)
        return False  # idle_add: one-shot

    def _sync_compose_from_ui(self) -> None:
        self.compose.set_to(self._to_entry.get_text())
        self.compose.set_cc(self._cc_entry.get_text())
        self.compose.set_bcc(self._bcc_entry.get_text())
        self.compose.set_subject(self._subject_entry.get_text())
        self.compose.include_signature = self._switch_signature.get_active()
        self.compose.markdown = self._switch_markdown.get_active()
        self.compose.encrypt = self._switch_encrypt.get_active()
        self.compose.sign = self._switch_sign.get_active()

    # -- from dropdown -------------------------------------------------------

    def _on_from_changed(self, drop, _spec) -> None:
        idx = drop.get_selected()
        accts = self.app.accounts.accounts
        if 0 <= idx < len(accts):
            self.account = accts[idx]
            self.compose.set_from(self.account)
            self._rebuild_preview()

    # -- send / status -------------------------------------------------------

    def _on_send_status(self, _src, warn: bool, msg: str) -> None:
        self._status_label.set_text(msg)
        self._status_revealer.set_reveal_child(True)
        if not warn and msg.endswith("successfully!"):
            GLib.timeout_add_seconds(2, lambda: (
                self._status_revealer.set_reveal_child(False), False)[1])

    def _on_message_sent(self, _src, ok: bool) -> None:
        self.sending = False
        if not ok:
            return
        self.message_sent = True
        log.info("em: message sent.")

        # save_sent action -> notmuch add
        saved = self.compose.saved_sent_path
        if saved is not None:
            extra = self.account.additional_sent_tags
            irt = self.compose.inreplyto or self.reply_source_mid \
                or self.forward_source_mid
            tag = "replied" if self.reply_source_mid else (
                "passed" if self.forward_source_mid else "replied")
            self.app.actions.doit(AddSentMessage(
                saved, extra_tags=extra, in_reply_to=irt, source_tag=tag))

        # delete draft if we had one
        if self.draft_msg is not None and self.draft_msg.in_notmuch:
            self.app.actions.doit(RemoveMessage(self.draft_msg.filename))
            self.draft_msg = None

        if self.app.config.config.get_bool("mail.close_on_success"):
            GLib.idle_add(lambda: (self.main_window.close_page(force=True),
                                   False)[1])

    # -- send key ------------------------------------------------------------

    def _do_send(self) -> bool:
        if self.sending:
            return True
        self._sync_compose_from_ui()
        if not self.compose.to.strip():
            self.ask_yes_no("No recipient. Send anyway?",
                            lambda yes: yes and self._send_now())
            return True
        self._send_now()
        return True

    def _send_now(self) -> None:
        self.sending = True
        self._sync_compose_from_ui()
        try:
            self.compose.build()
            self.compose.finalize()
        except Exception as e:
            log.error("em: build failed: %s", e)
            self.sending = False
            self._on_send_status(None, True, f"build failed: {e}")
            return
        self.compose.send_threaded()

    # -- draft handling --------------------------------------------------------

    def _save_draft(self) -> bool:
        self._sync_compose_from_ui()
        try:
            self.compose.build()
            self.compose.finalize()
        except Exception as e:
            log.error("em: draft build failed: %s", e)
            return False

        if self.draft_msg is not None and self.draft_msg.in_notmuch:
            target = Path(self.draft_msg.filename)
            self.compose.write_to_file(target)
            log.info("em: overwrote existing draft at %s", target)
        else:
            ddir = self.account.save_drafts_to
            if ddir is None:
                log.error("em: no save_drafts_to configured for account %s",
                          self.account.id)
                return False
            ddir.mkdir(parents=True, exist_ok=True)
            target = ddir / safe_fname(self.msg_id)
            self.compose.write_to_file(target)
            self.app.actions.doit(AddDraftMessage(target))

        self.draft_saved = True
        self._on_send_status(None, False, f"draft saved: {target}")
        return True

    def _delete_draft(self) -> bool:
        if self.draft_msg is None or not self.draft_msg.in_notmuch:
            log.warning("em: nothing to delete -- not a draft yet.")
            return True
        self.app.actions.doit(RemoveMessage(self.draft_msg.filename))
        self.draft_msg = None
        self.main_window.close_page(force=True)
        return True

    # -- raw view ------------------------------------------------------------

    def _view_raw(self) -> bool:
        self._sync_compose_from_ui()
        try:
            self.compose.build()
            self.compose.finalize()
        except Exception as e:
            log.error("em: raw build failed: %s", e)
            return True
        raw = self.compose.to_bytes()
        rm = RawMessage(self.main_window, content=raw, label="raw: " + self.msg_id)
        self.main_window.add_mode(rm)
        return True

    # -- attach --------------------------------------------------------------

    def _attach_file(self) -> bool:
        dlg = Gtk.FileDialog(title="Attach file")
        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if f is not None:
                p = f.get_path()
                if p:
                    self.compose.add_attachment(Attachment.from_file(p))
                    self._rebuild_preview()
        dlg.open(self.main_window, None, done)
        return True

    # -- editor cycle ---------------------------------------------------------

    def _toggle_editor(self) -> bool:
        if self.editor_active:
            return True
        cmd = self.app.config.config.get_str("editor.cmd")
        ed = ExternalEditor(cmd, self.tmpfile_path)
        ed.connect("edited", lambda *_: self._read_edited())
        ed.connect("stopped", self._on_editor_stopped)
        if ed.start():
            self.editor_active = True
        else:
            log.error("em: could not launch editor: %s", cmd)
            self.main_window.error_bell()
        return True

    def _on_editor_stopped(self, _ed) -> None:
        # always read once more on exit so the preview reflects the final
        # saved content even if the file monitor coalesced the last change
        self.editor_active = False
        self._read_edited()

    # -- close handling ------------------------------------------------------

    def pre_close(self) -> None:
        cfg = self.app.config.config
        if (not self.message_sent and not self.draft_saved
                and cfg.get_bool("editor.save_draft_on_force_quit")):
            log.warning("em: force quit, trying to save draft..")
            if not self._save_draft():
                log.error("em: cannot save draft! changes will be lost.")

        # clean up tmpfile
        try:
            if self.tmpfile_path.is_file():
                self.tmpfile_path.unlink()
        except OSError:
            pass

    # -- keys -----------------------------------------------------------------

    def _toggle_signature(self) -> bool:
        self._switch_signature.set_active(not self._switch_signature.get_active())
        self._rebuild_preview()
        return True

    def _toggle_markdown(self) -> bool:
        self._switch_markdown.set_active(not self._switch_markdown.get_active())
        self._rebuild_preview()
        return True

    def _toggle_encrypt(self) -> bool:
        """4-state cycle: None -> Sign -> Sign+Encrypt -> Encrypt -> None."""
        e = self._switch_encrypt.get_active()
        s = self._switch_sign.get_active()
        if not e and not s:
            self._switch_sign.set_active(True)
        elif not e and s:
            self._switch_encrypt.set_active(True)
        elif e and s:
            self._switch_sign.set_active(False)
        else:  # e and not s
            self._switch_encrypt.set_active(False)
        return True

    def _cycle_from(self) -> bool:
        accts = self.app.accounts.accounts
        if len(accts) < 2:
            return True
        i = (accts.index(self.account) + 1) % len(accts)
        self._from_drop.set_selected(i)
        return True

    def _cancel_send(self) -> bool:
        if self.sending:
            self.compose.cancel_sending()
        return True

    def _register_keys(self) -> None:
        k = self.keys
        k.title = "Edit message"
        k.register_key("Return", "edit_message.edit",
                       "Edit message in editor",
                       lambda _k: self._toggle_editor())
        k.register_key("y", "edit_message.send", "Send message",
                       lambda _k: self._do_send())
        k.register_key("C-c", "edit_message.cancel", "Cancel send",
                       lambda _k: self._cancel_send())
        k.register_key("V", "edit_message.view_raw", "View raw message",
                       lambda _k: self._view_raw())
        k.register_key("f", "edit_message.cycle_from",
                       "Cycle through From selector",
                       lambda _k: self._cycle_from())
        k.register_key("a", "edit_message.attach", "Attach file",
                       lambda _k: self._attach_file())
        # NOTE: typo preserved verbatim from C++ for keybinding-file compat
        k.register_key("A", "edit_messsage.attach_mids",
                       "Attach messages by id",
                       lambda _k: True)
        k.register_key("s", "edit_message.save_draft", "Save draft",
                       lambda _k: self._save_draft())
        k.register_key("D", "edit_message.delete_draft", "Delete draft",
                       lambda _k: self._delete_draft())
        k.register_key("S", "edit_message.toggle_signature",
                       "Toggle signature",
                       lambda _k: self._toggle_signature())
        k.register_key("M", "edit_message.toggle_markdown",
                       "Toggle markdown",
                       lambda _k: self._toggle_markdown())
        k.register_key("E", "edit_message.toggle_encrypt",
                       "Cycle encrypt/sign",
                       lambda _k: self._toggle_encrypt())


# -- mailto: parser ---------------------------------------------------------

def parse_mailto(uri: str) -> dict:
    """Tiny mailto: URL parser used by --mailto and thread_view link clicks."""
    if uri.startswith("mailto:"):
        rest = uri[len("mailto:"):]
    else:
        rest = uri

    to, _, query = rest.partition("?")
    out = {"to": unquote(to), "cc": "", "bcc": "", "subject": "", "body": ""}
    if not query:
        return out
    for k, v in parse_qsl(query, keep_blank_values=True):
        k = k.lower()
        if k in out:
            out[k] = v
    return out
