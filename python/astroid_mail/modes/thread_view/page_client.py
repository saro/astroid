"""Port of src/modes/thread_view/page_client.cc.

Replaces the unix-socket/protobuf transport with JSON over
webkit_web_view_evaluate_javascript (Python -> JS, ack in the return
value) and a script message handler (JS -> Python events).
"""

from __future__ import annotations

import base64
import json
from importlib import resources

from gi.repository import GLib

from ...log import log
from ...utils.dates import pretty_date, pretty_verbose_date
from ...utils.misc import format_size
from ...utils.address import Address, AddressList
from ...utils import tags as tagutils

MAX_PREVIEW_LEN = 80


def load_tv_js() -> str:
    return (resources.files("astroid_mail.modes.thread_view")
            / "data" / "tv.js").read_text(encoding="utf-8")


class ElementType:
    Empty = "empty"
    Address = "address"
    Part = "part"
    Attachment = "attachment"
    MimeMessage = "mime_message"
    Encryption = "encryption"


class Element:
    def __init__(self, type_: str, id_: int, mid: str, focusable: bool = True):
        self.type = type_
        self.id = id_
        self.mid = mid
        self.focusable = focusable

    def sid(self) -> str:
        prefix = {"part": "part", "attachment": "attachment",
                  "mime_message": "mime", "encryption": "encrypt"}.get(
                      self.type, "el")
        return f"{prefix}_{self.mid}_{self.id}"

    def to_json(self) -> dict:
        return {"type": self.type, "id": self.id, "sid": self.sid(),
                "focusable": self.focusable}


class PageClient:
    def __init__(self, thread_view):
        self.thread_view = thread_view
        self.ready = False
        self.enable_gravatar = False

    # -- transport -----------------------------------------------------------

    def send(self, msg: dict, on_ack=None) -> None:
        """Evaluate Astroid.handle(msg) in the page; ack via callback."""
        script = f"JSON.stringify(Astroid.handle({json.dumps(msg)}))"

        def cb(webview, result):
            try:
                value = webview.evaluate_javascript_finish(result)
                ackd = json.loads(value.to_string())
                self._handle_ack(ackd)
                if on_ack:
                    on_ack(ackd)
            except GLib.Error as e:
                log.error("pc: evaluate_javascript failed: %s", e)

        self.thread_view.webview.evaluate_javascript(
            script, -1, None, None, None, cb)

    def _handle_ack(self, ack: dict) -> None:
        focus = ack.get("focus") or {}
        mid = focus.get("mid")
        element = focus.get("element", -1)
        if mid and element >= 0:
            self.thread_view.on_focus_changed(mid, element)

    # -- protocol --------------------------------------------------------------

    def load(self, theme) -> None:
        log.debug("pc: sending page..")
        allowed = [self.thread_view.home_uri]
        if self.enable_gravatar:
            allowed.append("https://www.gravatar.com/avatar/")
        self.send({
            "type": "page",
            "css": theme.thread_view_css,
            "part_css": theme.part_css,
            "allowed_uris": allowed,
        })

    def clear_messages(self) -> None:
        self.send({"type": "clear_messages"})

    def update_state(self) -> None:
        tv = self.thread_view
        msgs = []
        for m in tv.mthread.messages:
            st = tv.state[m]
            msgs.append({
                "mid": m.safe_mid(),
                "level": m.level,
                "elements": [e.to_json() for e in st["elements"]],
            })
        self.send({"type": "state", "edit_mode": tv.edit_mode,
                   "messages": msgs})

    def add_message(self, m) -> None:
        self.send({"type": "add_message", "message": self.make_message(m)})

    def update_message(self, m, kind: str) -> None:
        self.send({"type": "update_message",
                   "message": self.make_message(m, keep_state=True),
                   "kind": kind})

    def remove_message(self, m) -> None:
        self.send({"type": "remove_message", "mid": m.safe_mid()})

    def set_marked_state(self, m, marked: bool) -> None:
        self.send({"type": "mark", "mid": m.safe_mid(), "marked": marked})

    def set_hidden_state(self, m, hidden: bool) -> None:
        self.send({"type": "hidden", "mid": m.safe_mid(), "hidden": hidden})

    def set_indent(self, indent: bool) -> None:
        self.send({"type": "indent", "indent": indent})

    def allow_remote_resources(self) -> None:
        self.send({"type": "allow_remote_images", "allow": True})

    def set_remote_images(self, allow: bool) -> None:
        """Enable/disable remote (inline) images for the whole view, then
        re-render every message so the iframe CSP is rebuilt."""
        self.send({"type": "allow_remote_images", "allow": allow})
        for m in self.thread_view.mthread.messages:
            self.update_message(m, "visible_parts")

    def toggle_html(self, m) -> bool:
        """Switch the focused message between its text/plain and text/html
        alternative parts. Returns False if the message has no such
        alternative to toggle."""
        if m is None or not self._has_html_alternative(m):
            return False
        st = self.thread_view.state.setdefault(m, {})
        st["prefer_html"] = not st.get("prefer_html", False)
        self.update_message(m, "visible_parts")
        return True

    @staticmethod
    def _has_html_alternative(m) -> bool:
        types = {c.mime_type for c in m.all_parts()
                 if c.viewable and c.siblings}
        return "text/html" in types and "text/plain" in types

    def set_focus(self, m, element: int) -> None:
        if m is None:
            return
        self.send({"type": "focus", "mid": m.safe_mid(),
                   "focus": True, "element": element})

    def set_warning(self, m, txt: str, show: bool = True) -> None:
        self.send({"type": "info", "mid": m.safe_mid(), "warning": True,
                   "set": show, "txt": txt})

    def set_info(self, m, txt: str, show: bool = True) -> None:
        self.send({"type": "info", "mid": m.safe_mid(), "warning": False,
                   "set": show, "txt": txt})

    def navigate(self, direction: str, kind: str, mid: str = "",
                 element: int = 0, focus_top: bool = False) -> None:
        self.send({"type": "navigate", "direction": direction, "kind": kind,
                   "mid": mid, "element": element, "focus_top": focus_top})

    # -- message serialization (port of make_message/build_mime_tree) -----------

    def make_message(self, m, keep_state: bool = False) -> dict:
        tv = self.thread_view
        st = tv.state.setdefault(m, {"elements": [Element(ElementType.Empty, -1,
                                                          m.safe_mid())],
                                     "marked": False, "expanded": True,
                                     "current_element": 0,
                                     "unread_checked": False,
                                     "prefer_html": False})
        prefer_html = st.get("prefer_html", False)

        def addr(a: str) -> dict:
            ad = Address(a)
            return {"name": ad.name(), "email": ad.email(),
                    "full_address": ad.full_address()}

        def addrlist(raw: str) -> list:
            return [{"name": a.name(), "email": a.email(),
                     "full_address": a.full_address()}
                    for a in AddressList(raw)]

        preview = m.plain_text(fallback_html=True)
        preview = preview.replace("\n", " ").strip()
        if len(preview) > MAX_PREVIEW_LEN:
            preview = preview[:MAX_PREVIEW_LEN - 3] + "..."

        cfg = tv.config

        tag_string = tagutils.concat_tags_color(m.tags, pango=False)

        msg = {
            "mid": m.safe_mid(),
            "sender": addr(m.sender),
            "to": addrlist(m.to()),
            "cc": addrlist(m.cc()),
            "bcc": addrlist(m.bcc()),
            "reply_to": addr(m.reply_to) if m.reply_to else None,
            "date_pretty": pretty_date(m.time),
            "date_verbose": pretty_verbose_date(m.time),
            "subject": m.subject,
            "tags": list(m.tags),
            "tag_string": tag_string,
            "gravatar": "",
            "missing_content": m.missing_content,
            "patch": False,
            "level": m.level,
            "in_reply_to": m.inreplyto,
            "preview": preview,
            "root": None,
            "mime_messages": [],
            "attachments": [],
        }

        if not m.missing_content and m.root is not None:
            msg["root"] = self._build_chunk(m, m.root, st, keep_state,
                                            prefer_html)

            for c in m.attachments():
                a = self._chunk_summary(c)
                data = c.contents()
                a["thumbnail"] = ""
                if c.content_id:
                    a["content"] = ("data:%s;base64,%s" % (
                        c.mime_type, base64.b64encode(data).decode()))
                msg["attachments"].append(a)

                if not keep_state:
                    st["elements"].append(
                        Element(ElementType.Attachment, c.id, m.safe_mid()))

        if not keep_state and msg["attachments"]:
            # attachments come right after the message header in j/k order
            # (attachments are also displayed before the body): reorder to
            # Empty/MimeMessage -> Attachment -> Part/Encryption
            els = st["elements"]
            front = [e for e in els
                     if e.type in (ElementType.Empty, ElementType.MimeMessage)]
            atts = [e for e in els if e.type == ElementType.Attachment]
            rest = [e for e in els
                    if e.type not in (ElementType.Empty,
                                      ElementType.MimeMessage,
                                      ElementType.Attachment)]
            st["elements"] = front + atts + rest

        return msg

    def _chunk_summary(self, c) -> dict:
        size = c.get_file_size()

        # crypto status only on the wrapper chunk itself, so the thread view
        # shows exactly one marker per multipart/encrypted|signed container
        # (kids inherit the crypt object but must not repeat the marker)
        signature = None
        encryption = None
        if c.crypt is not None and c.mime_type in ("multipart/encrypted",
                                                   "multipart/signed"):
            if c.crypt.verify_tried:
                signature = {
                    "verified": c.crypt.verified,
                    "sign_strings": list(c.crypt.sign_strings),
                    "all_errors": list(c.crypt.sig_errors),
                }
            if c.crypt.decrypt_tried:
                encryption = {
                    "decrypted": c.crypt.decrypted,
                    "enc_strings": list(c.crypt.enc_strings),
                }

        return {
            "id": c.id,
            "sid": str(c.id),
            "mime_type": c.mime_type,
            "cid": c.content_id,
            "viewable": c.viewable,
            "preferred": c.preferred,
            "attachment": c.attachment,
            "is_encrypted": c.isencrypted,
            "is_signed": c.issigned,
            "signature": signature,
            "encryption": encryption,
            "sibling": bool(c.siblings),
            "use": True,
            "focusable": True,
            "content": "",
            "filename": c.get_filename(),
            "size": size,
            "human_size": format_size(size),
            "thumbnail": "",
            "kids": [],
            "siblings": [],
        }

    @staticmethod
    def _chosen_sibling(c, prefer_html: bool):
        """Which chunk of a sibling group (multipart/alternative) to show."""
        group = [c] + list(c.siblings)
        if prefer_html:
            html = next((g for g in group if g.mime_type == "text/html"), None)
            if html is not None:
                return html
        chosen = next((g for g in group if g.preferred), None)
        return chosen if chosen is not None else group[0]

    def _build_chunk(self, m, c, st, keep_state: bool,
                     prefer_html: bool = False) -> dict | None:
        if c.attachment:
            return None

        part = self._chunk_summary(c)

        if c.viewable:
            part["content"] = c.viewable_text(html=True)
            if not c.siblings:
                use = True
            else:
                use = c is self._chosen_sibling(c, prefer_html)
            part["use"] = use

            if not keep_state:
                el = Element("part", c.id, m.safe_mid(), focusable=not use)
                st["elements"].append(el)
                part["focusable"] = el.focusable

        for k in c.kids:
            ck = self._build_chunk(m, k, st, keep_state, prefer_html)
            if ck is not None:
                part["kids"].append(ck)

        return part
