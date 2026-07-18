"""Port of src/modes/thread_view/thread_view.cc (read path).

WebKitGTK 6.0: per-view UserContentManager + ephemeral NetworkSession;
tv.js injected as a UserScript does the DOM work (no web-process
extension); link clicks intercepted via decide-policy.
"""

from __future__ import annotations

import json
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import GLib, Gtk, WebKit  # noqa: E402

from ...actions import TagAction, ToggleAction  # noqa: E402
from ...db import Db, MessageSummary, ThreadSummary  # noqa: E402
from ...log import log  # noqa: E402
from ...models.message_thread import MessageThread  # noqa: E402
from ...utils.misc import random_alphanumeric  # noqa: E402
from ..mode import Mode  # noqa: E402
from .page_client import PageClient, load_tv_js  # noqa: E402
from .theme import Theme  # noqa: E402


class ThreadView(Mode):
    def __init__(self, main_window, edit_mode: bool = False):
        super().__init__(main_window)
        app = main_window.app

        self.edit_mode = edit_mode
        self.wk_loaded = False
        self.ready = False

        self.thread: ThreadSummary | None = None
        self.mthread: MessageThread | None = None
        self.focused_message = None
        self.state: dict = {}
        self.remote_images_allowed = False

        self.config = app.config
        tvc = app.config.config
        self.indent_messages = tvc.get_bool("thread_view.indent_messages")
        self.open_external_link = tvc.get_str("thread_view.open_external_link")
        self.expand_flagged = tvc.get_bool("thread_view.expand_flagged")
        self.unread_delay = tvc.get_float("thread_view.mark_unread_delay")

        self.home_uri = "file:///astroid/%s/" % random_alphanumeric(40)

        Theme.load()

        # webkit setup
        self.ucm = WebKit.UserContentManager()
        self.ucm.register_script_message_handler("astroid", None)
        self.ucm.connect("script-message-received::astroid", self._on_js_event)
        self.ucm.add_script(WebKit.UserScript.new(
            load_tv_js(),
            WebKit.UserContentInjectedFrames.TOP_FRAME,
            WebKit.UserScriptInjectionTime.START, None, None))

        self.session = WebKit.NetworkSession.new_ephemeral()
        self.webview = WebKit.WebView(user_content_manager=self.ucm,
                                      network_session=self.session)

        s = self.webview.get_settings()
        s.set_enable_javascript(True)
        s.set_enable_html5_database(False)
        s.set_enable_html5_local_storage(False)
        s.set_media_playback_requires_user_gesture(True)
        s.set_zoom_text_only(True)

        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)
        self.append(self.webview)

        self.page_client = PageClient(self)
        self.page_client.enable_gravatar = tvc.get_bool("thread_view.gravatar.enable")

        self.webview.connect("load-changed", self._on_load_changed)
        self.webview.connect("decide-policy", self._on_decide_policy)

        self.register_keys()

        self.webview.load_html(Theme.thread_view_html, self.home_uri)

    # -- loading ------------------------------------------------------------

    def _on_load_changed(self, webview, event) -> None:
        if event == WebKit.LoadEvent.FINISHED:
            log.debug("tv: load finished.")
            self.wk_loaded = True
            self.page_client.load(Theme)
            self.ready = True
            if self.mthread is not None:
                self.render_messages()

    def load_thread(self, thread: ThreadSummary) -> None:
        log.info("tv: load thread: %s", thread.thread_id)
        self.thread = thread
        self.set_label(thread.subject or thread.thread_id)

        with Db(Db.READ_ONLY) as db:
            mt = MessageThread(thread)
            mt.load_messages(db)
        self.load_message_thread(mt)

    def load_message_thread(self, mt: MessageThread) -> None:
        self.mthread = mt
        if mt.subject:
            self.set_label(mt.subject)
        if self.ready:
            self.render_messages()

    def render_messages(self) -> None:
        self.page_client.clear_messages()
        self.state = {}
        self.focused_message = None

        for m in self.mthread.messages:
            self.page_client.add_message(m)

        self.page_client.update_state()
        if self.indent_messages:
            self.page_client.set_indent(True)

        # expand unread or flagged; focus first unread, else newest
        focus_msg = None
        for m in self.mthread.messages:
            unread = "unread" in m.tags
            flagged = "flagged" in m.tags
            expand = unread or (flagged and self.expand_flagged) \
                or len(self.mthread.messages) == 1 \
                or m is self.mthread.messages[-1]
            if not expand and not self.edit_mode:
                self.page_client.set_hidden_state(m, True)
                if m in self.state:
                    self.state[m]["expanded"] = False
            if unread and focus_msg is None:
                focus_msg = m

        if focus_msg is None and self.mthread.messages:
            focus_msg = self.mthread.messages[-1]
        if focus_msg is not None:
            self.focus_message(focus_msg)

    # -- events ------------------------------------------------------------------

    def _on_js_event(self, ucm, js_value) -> None:
        try:
            ev = json.loads(js_value.to_string())
        except ValueError:
            return
        if ev.get("event") == "debug":
            log.debug("tv: js: %s", ev.get("msg"))
        elif ev.get("event") == "focus_changed":
            self.on_focus_changed(ev.get("mid"), ev.get("element", 0))

    def on_focus_changed(self, mid: str, element: int) -> None:
        for m in (self.mthread.messages if self.mthread else []):
            if m.safe_mid() == mid:
                if self.focused_message is not m:
                    self.focused_message = m
                    self._schedule_unread_check(m)
                if m in self.state:
                    self.state[m]["current_element"] = element
                break

    def _on_decide_policy(self, webview, decision, decision_type) -> bool:
        if decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            nav = decision.get_navigation_action()
            if nav.get_navigation_type() == WebKit.NavigationType.LINK_CLICKED:
                decision.ignore()
                uri = nav.get_request().get_uri()
                log.info("tv: navigating to: %s", uri)
                self.link_clicked(uri)
                return True
            # allow initial load_html
            return False
        decision.ignore()
        return True

    def link_clicked(self, uri: str) -> None:
        scheme = GLib.uri_parse_scheme(uri) or ""
        if scheme in ("http", "https", "ftp"):
            self.open_link(uri)
        elif scheme == "mailto":
            self.main_window.app.open_mailto(uri)
        else:
            log.error("tv: unknown uri scheme. not opening.")

    def open_link(self, uri: str) -> None:
        log.debug("tv: opening: %s", uri)
        import subprocess
        try:
            subprocess.Popen([self.open_external_link, uri])
        except OSError as e:
            log.error("tv: failed to open link: %s", e)

    # -- unread handling -----------------------------------------------------------

    def _schedule_unread_check(self, m) -> None:
        if self.edit_mode or self.unread_delay < 0:
            return
        st = self.state.get(m)
        if st is None or st.get("unread_checked"):
            return
        delay = max(int(self.unread_delay * 1000), 1)
        GLib.timeout_add(delay, self._unread_check, m)

    def _unread_check(self, m) -> bool:
        if self.focused_message is m and "unread" in m.tags:
            st = self.state.get(m)
            if st is not None:
                st["unread_checked"] = True
            self.main_window.app.actions.doit(
                TagAction(self._summary_of(m), remove=["unread"]))
        return False  # one-shot

    def _summary_of(self, m) -> MessageSummary:
        return MessageSummary(mid=m.mid, thread_id=(
            self.thread.thread_id if self.thread else ""), tags=list(m.tags))

    # -- keybindings ------------------------------------------------------------

    def register_keys(self) -> None:
        keys = self.keys
        keys.title = "Thread View"
        pc = self.page_client

        keys.register_key("j", "thread_view.down",
                          "Scroll down or move focus to next element",
                          lambda k: (pc.navigate("down", "visual_element"), True)[1],
                          aliases=["Down"])
        keys.register_key("C-j", "thread_view.next_element",
                          "Move focus to next element",
                          lambda k: (pc.navigate("down", "element"), True)[1])
        keys.register_key("J", "thread_view.scroll_down_big",
                          "Scroll down",
                          lambda k: (pc.navigate("down", "visual_big"), True)[1])
        keys.register_key("C-d", "thread_view.page_down", "Page down",
                          lambda k: (pc.navigate("down", "visual_page"), True)[1],
                          aliases=["Page_Down"])
        # first-class binding (not an alias of page_down) so a user override
        # of thread_view.page_down cannot silently drop the space key
        keys.register_key("space", "thread_view.spacebar_down",
                          "Page down (spacebar)",
                          lambda k: (log.debug("tv: spacebar page down"),
                                     pc.navigate("down", "visual_page"),
                                     True)[2])

        keys.register_key("k", "thread_view.up",
                          "Scroll up or move focus to previous element",
                          lambda k: (pc.navigate("up", "visual_element"), True)[1],
                          aliases=["Up"])
        keys.register_key("C-k", "thread_view.previous_element",
                          "Move focus to previous element",
                          lambda k: (pc.navigate("up", "element"), True)[1])
        keys.register_key("K", "thread_view.scroll_up_big", "Scroll up",
                          lambda k: (pc.navigate("up", "visual_big"), True)[1])
        keys.register_key("C-u", "thread_view.page_up", "Page up",
                          lambda k: (pc.navigate("up", "visual_page"), True)[1],
                          aliases=["Page_Up"])
        keys.register_key("S-space", "thread_view.spacebar_up",
                          "Page up (shift+spacebar)",
                          lambda k: (log.debug("tv: spacebar page up"),
                                     pc.navigate("up", "visual_page"),
                                     True)[2])

        keys.register_key("1", "thread_view.home", "Scroll home",
                          lambda k: (pc.navigate("up", "extreme"), True)[1],
                          aliases=["Home"])
        keys.register_key("0", "thread_view.end", "Scroll to end",
                          lambda k: (pc.navigate("down", "extreme"), True)[1],
                          aliases=["End"])

        keys.register_key("n", "thread_view.next_message", "Focus next message",
                          lambda k: (pc.navigate("down", "message"), True)[1])
        keys.register_key("p", "thread_view.previous_message",
                          "Focus previous message",
                          lambda k: (pc.navigate("up", "message"), True)[1])

        keys.register_key("e", "thread_view.expand",
                          "Toggle expand on focused message",
                          self._key_toggle_expand)
        keys.register_key("Return", "thread_view.activate",
                          "Open focused element (attachment) or "
                          "toggle expand on the message",
                          self._key_activate,
                          aliases=["KP_Enter"])
        keys.register_key("C-e", "thread_view.toggle_expand_all",
                          "Toggle expand on all messages",
                          self._key_toggle_expand_all)

        keys.register_key("t", "thread_view.mark",
                          "Mark or unmark current message",
                          self._key_mark)

        keys.register_key("N", "thread_view.toggle_unread",
                          "Toggle the unread tag on the message",
                          self._key_toggle_unread)
        keys.register_key("*", "thread_view.flag",
                          "Toggle the 'flagged' tag on the message",
                          self._key_toggle_flagged)
        keys.register_key("a", "thread_view.archive_thread",
                          "Toggle 'inbox' tag on the whole thread",
                          self._key_archive)
        keys.register_key("#", "thread_view.trash_thread",
                          "Toggle 'trash' tag on the whole thread",
                          self._key_trash)

        keys.register_key("$", "thread_view.refresh",
                          "Reload everything",
                          self._key_refresh)

        keys.register_key("r", "thread_view.reply",
                          "Reply to current message",
                          self._key_reply)
        keys.register_key("G", "thread_view.reply_all",
                          "Reply all to current message",
                          self._key_reply_all)
        keys.register_key("R", "thread_view.reply_sender",
                          "Reply to sender only",
                          self._key_reply_sender)
        keys.register_key("f", "thread_view.forward",
                          "Forward current message",
                          self._key_forward)
        keys.register_key("V", "thread_view.raw_message",
                          "View raw message",
                          self._key_raw)

        keys.register_key("H", "thread_view.toggle_html",
                          "Switch between the plain and HTML version",
                          self._key_toggle_html)
        keys.register_key("I", "thread_view.toggle_remote_images",
                          "Toggle showing inline (remote) images",
                          self._key_toggle_remote_images)

    def _key_toggle_html(self, k) -> bool:
        m = self.focused_message
        if m is None:
            return True
        if not self.page_client.toggle_html(m):
            log.info("tv: no HTML/plain alternative to switch for this message")
        return True

    def _key_toggle_remote_images(self, k) -> bool:
        # gate on encryption like the C++ (allow_remote_when_encrypted)
        if not self.remote_images_allowed:
            allow_enc = self.config.config.get_bool(
                "thread_view.allow_remote_when_encrypted")
            if not allow_enc and self.mthread is not None:
                if any(any(c.isencrypted for c in msg.all_parts())
                       for msg in self.mthread.messages):
                    log.warning("tv: not showing remote images: thread has "
                                "encrypted parts (set "
                                "thread_view.allow_remote_when_encrypted)")
                    return True
        self.remote_images_allowed = not self.remote_images_allowed
        log.info("tv: remote images %s",
                 "enabled" if self.remote_images_allowed else "disabled")
        self.page_client.set_remote_images(self.remote_images_allowed)
        return True

    def _key_activate(self, k) -> bool:
        """Enter: open the focused element if it is an attachment, else
        toggle expand on the message (port of element_action EEnter)."""
        m = self.focused_message
        if m is None:
            return True
        st = self.state.get(m, {})
        idx = st.get("current_element", 0)
        elements = st.get("elements", [])
        if 0 < idx < len(elements):
            el = elements[idx]
            if el.type == "attachment":
                return self._open_attachment(m, el.id)
        return self._key_toggle_expand(k)

    def _open_attachment(self, m, chunk_id: int) -> bool:
        """Save the attachment to a tmp dir and open it with
        attachment.external_open_cmd (xdg-open by default)."""
        import subprocess
        import tempfile

        c = m.get_chunk_by_id(chunk_id)
        if c is None:
            log.error("tv: no chunk %s in message %s", chunk_id, m.mid)
            return True

        from ...utils.misc import safe_fname
        tmpdir = Path(tempfile.mkdtemp(prefix="astroid-attachment-"))
        fname = safe_fname(c.get_filename() or f"attachment-{c.id}")
        target = tmpdir / fname
        try:
            c.save_to(target)
        except OSError as e:
            log.error("tv: could not save attachment: %s", e)
            return True

        cmd = self.config.config.get_str("attachment.external_open_cmd",
                                         "xdg-open")
        log.info("tv: opening attachment %s with %s", target, cmd)
        try:
            subprocess.Popen([cmd, str(target)])
        except OSError as e:
            log.error("tv: could not open attachment: %s", e)
        return True

    def _key_toggle_expand(self, k) -> bool:
        m = self.focused_message
        if m is None or self.edit_mode:
            return True
        st = self.state.get(m, {})
        expanded = st.get("expanded", True)
        st["expanded"] = not expanded
        self.page_client.set_hidden_state(m, expanded)
        return True

    def _key_toggle_expand_all(self, k) -> bool:
        if self.edit_mode or not self.mthread:
            return True
        all_expanded = all(self.state.get(m, {}).get("expanded", True)
                           for m in self.mthread.messages)
        for m in self.mthread.messages:
            self.state.setdefault(m, {})["expanded"] = not all_expanded
            self.page_client.set_hidden_state(m, all_expanded)
        return True

    def _key_mark(self, k) -> bool:
        m = self.focused_message
        if m is None:
            return True
        st = self.state.setdefault(m, {})
        st["marked"] = not st.get("marked", False)
        self.page_client.set_marked_state(m, st["marked"])
        self.page_client.navigate("down", "message")
        return True

    def _toggle_tag_message(self, tag: str) -> bool:
        m = self.focused_message
        if m is None or self.edit_mode:
            return True
        self.main_window.app.actions.doit(
            ToggleAction(self._summary_of(m), tag))
        if tag == "unread":
            self.state.setdefault(m, {})["unread_checked"] = True
        return True

    def _key_toggle_unread(self, k) -> bool:
        return self._toggle_tag_message("unread")

    def _key_toggle_flagged(self, k) -> bool:
        return self._toggle_tag_message("flagged")

    def _toggle_tag_thread(self, tag: str) -> bool:
        if self.edit_mode or self.thread is None:
            return True
        self.main_window.app.actions.doit(ToggleAction(self.thread, tag))
        return True

    def _key_archive(self, k) -> bool:
        return self._toggle_tag_thread("inbox")

    def _key_trash(self, k) -> bool:
        return self._toggle_tag_thread("trash")

    def _key_refresh(self, k) -> bool:
        Theme.load(reload=True)
        self.webview.load_html(Theme.thread_view_html, self.home_uri)
        self.ready = False
        return True

    # -- reply / forward / raw -------------------------------------------------

    def _reply_in_mode(self, mode_value) -> bool:
        from ..edit_message import EditMessage
        from ...reply import (ReplyMode, derive_recipients, references_for_reply,
                              reply_quote_body, reply_subject)
        m = self.focused_message
        if m is None:
            return True
        cfg = self.main_window.app.config
        accts = self.main_window.app.accounts
        to, cc, bcc = derive_recipients(mode_value, m, accts,
                                        cfg.config.get_bool(
                                            "mail.reply.mailinglist_reply_to_sender"))
        refs, irt = references_for_reply(m)
        em = EditMessage(self.main_window,
                         to=to, cc=cc, bcc=bcc,
                         subject=reply_subject(m.subject),
                         body=reply_quote_body(cfg, m),
                         references=refs, inreplyto=irt,
                         reply_source_mid=m.mid)
        self.main_window.add_mode(em)
        return True

    def _key_reply(self, k) -> bool:
        from ...reply import ReplyMode
        return self._reply_in_mode(ReplyMode.Default)

    def _key_reply_all(self, k) -> bool:
        from ...reply import ReplyMode
        return self._reply_in_mode(ReplyMode.All)

    def _key_reply_sender(self, k) -> bool:
        from ...reply import ReplyMode
        return self._reply_in_mode(ReplyMode.Sender)

    def _key_forward(self, k) -> bool:
        from ..edit_message import EditMessage
        from ...forward import (FwdDisposition, forward_attachments,
                                forward_as_attachment, forward_inline_body,
                                forward_subject, resolve_disposition)
        m = self.focused_message
        if m is None:
            return True
        cfg = self.main_window.app.config
        disp = resolve_disposition(cfg, FwdDisposition.Default)
        if disp == FwdDisposition.Attach:
            em = EditMessage(self.main_window,
                             subject=forward_subject(m.subject),
                             forward_source_mid=m.mid)
            em.compose.add_attachment(forward_as_attachment(m))
        else:
            em = EditMessage(self.main_window,
                             subject=forward_subject(m.subject),
                             body=forward_inline_body(cfg, m),
                             forward_source_mid=m.mid)
            for a in forward_attachments(m):
                em.compose.add_attachment(a)
        self.main_window.add_mode(em)
        return True

    def _key_raw(self, k) -> bool:
        from ..raw_message import RawMessage
        m = self.focused_message
        if m is None:
            return True
        self.main_window.add_mode(RawMessage.from_message(self.main_window, m))
        return True

    # -- focus -----------------------------------------------------------------

    def focus_message(self, m) -> None:
        self.focused_message = m
        self.page_client.set_focus(m, 0)
        self._schedule_unread_check(m)

    # -- mode interface ----------------------------------------------------------

    def grab_modal(self) -> None:
        # Keep keyboard focus on the mode widget (not the WebKitWebView):
        # all navigation is driven by JS via page_client, and a normal
        # focusable widget guarantees the window key controller receives
        # every keystroke (a focused webview can swallow keys natively).
        self.set_focusable(True)
        self.grab_focus()

    def on_message_changed(self, db: Db, mid: str) -> None:
        """thread-changed/message-updated handler: refresh tags display."""
        if not self.mthread:
            return
        for m in self.mthread.messages:
            if m.mid == mid:
                m.refresh_tags(db)
                self.page_client.update_message(m, "tags")
                break
