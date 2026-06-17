"""Port of src/message_thread.cc (read path).

Message wraps one mail file (parsed with GMime) plus its notmuch state;
MessageThread holds the ordered messages of one notmuch thread.
"""

from __future__ import annotations

import re
from pathlib import Path

import gi

gi.require_version("GMime", "3.0")
from gi.repository import GMime  # noqa: E402

from ..db import Db  # noqa: E402
from ..log import log  # noqa: E402
from ..utils.address import Address, AddressList  # noqa: E402
from .chunk import Chunk  # noqa: E402

GMime.init()


class MessageError(Exception):
    pass


def _parse_file(path: str) -> GMime.Message:
    stream = GMime.StreamFile.open(str(path), "r")
    parser = GMime.Parser.new_with_stream(stream)
    msg = parser.construct_message(None)
    if msg is None:
        raise MessageError(f"failed to parse message: {path}")
    return msg


class Message:
    def __init__(self, filename: str = "", mid: str = "",
                 preferred_type: str = "plain",
                 nm_summary=None):
        self.filename = filename
        self.mid = mid
        self.tags: list[str] = []
        self.in_notmuch = False
        self.missing_content = False
        self.level = 0
        self.time = 0

        self.subject = ""
        self.sender = ""
        self.inreplyto = ""
        self.references = ""
        self.reply_to = ""
        self._to = ""
        self._cc = ""
        self._bcc = ""

        self.root: Chunk | None = None
        self.preferred_type = preferred_type

        if nm_summary is not None:
            self.mid = nm_summary.mid
            self.filename = nm_summary.filename
            self.tags = list(nm_summary.tags)
            self.time = nm_summary.time
            self.in_notmuch = True

        if self.filename and Path(self.filename).is_file():
            self.load()
        else:
            if self.filename:
                log.error("failed to open file: %s, it does not exist!",
                          self.filename)
            self.missing_content = True

    # -- loading ---------------------------------------------------------------

    def load(self) -> None:
        gm = _parse_file(self.filename)
        self._gm = gm

        self.subject = gm.get_subject() or ""

        frm = gm.get_from()
        self.sender = frm.to_string(GMime.FormatOptions.get_default(), False) \
            if frm and frm.length() > 0 else ""

        if not self.mid:
            self.mid = gm.get_message_id() or ""

        self.inreplyto = gm.get_header("In-Reply-To") or ""
        self.references = gm.get_header("References") or ""
        rt = gm.get_reply_to()
        self.reply_to = rt.to_string(GMime.FormatOptions.get_default(), False) \
            if rt and rt.length() > 0 else ""

        def addrs(t):
            al = gm.get_addresses(t)
            return al.to_string(GMime.FormatOptions.get_default(), False) \
                if al and al.length() > 0 else ""

        self._to = addrs(GMime.AddressType.TO)
        self._cc = addrs(GMime.AddressType.CC)
        self._bcc = addrs(GMime.AddressType.BCC)

        if not self.time:
            dt = gm.get_date()
            self.time = int(dt.to_unix()) if dt else 0

        part = gm.get_mime_part()
        self.root = Chunk(part, self.preferred_type) if part else None
        if self.root is None:
            self.missing_content = True

    # -- accessors ---------------------------------------------------------------

    def to(self) -> str:
        return self._to

    def cc(self) -> str:
        return self._cc

    def bcc(self) -> str:
        return self._bcc

    def safe_mid(self) -> str:
        return re.sub(r"[^0-9A-Za-z.\-_@]", "_", self.mid)

    def all_parts(self) -> list[Chunk]:
        return self.root.all_parts() if self.root else []

    def viewable_parts(self) -> list[Chunk]:
        return [c for c in self.all_parts() if c.viewable]

    def attachments(self) -> list[Chunk]:
        return [c for c in self.all_parts() if c.attachment]

    def get_chunk_by_id(self, cid: int) -> Chunk | None:
        for c in self.all_parts():
            if c.id == cid:
                return c
        return None

    def plain_text(self, fallback_html: bool = False) -> str:
        """First text/plain part (for previews)."""
        for c in self.all_parts():
            if c.viewable and c.mime_type == "text/plain":
                return c.viewable_text(html=False)
        if fallback_html:
            for c in self.all_parts():
                if c.viewable:
                    return c.viewable_text(html=False)
        return ""

    def raw_contents(self) -> bytes:
        try:
            return Path(self.filename).read_bytes()
        except OSError as e:
            log.error("message: could not read raw: %s", e)
            return b""

    # -- reply / forward helpers ---------------------------------------------

    def list_post(self) -> str:
        """Value of List-Post header (often '<mailto:list@...>')."""
        if not hasattr(self, "_gm") or self._gm is None:
            return ""
        return self._gm.get_header("List-Post") or ""

    def is_list_post(self) -> bool:
        return bool(self.list_post())

    def quote(self, quote_processor: str = "w3m -dump -T text/html") -> str:
        """Plain-text quotable body (port of Message::quote()).

        For HTML-only parts the configured quote_processor (default w3m)
        converts to text; siblings of HTML/plain are skipped unless
        they're the chosen text/plain or there is no other choice.
        """
        from ..utils.cmd import Cmd

        out: list[str] = []
        chunks = self.all_parts()

        def is_text(c, sub):
            return c.viewable and c.mime_type == f"text/{sub}"

        def app(c):
            use = False
            if c.siblings:
                if is_text(c, "plain"):
                    use = True
                elif all(s.mime_type not in ("text/plain", "text/html")
                         for s in c.siblings):
                    use = True
            else:
                use = True

            if use:
                if is_text(c, "html"):
                    if quote_processor:
                        ok, html_to_text, _ = Cmd.pipe(
                            quote_processor, c.viewable_text(html=False))
                        if ok:
                            out.append(html_to_text)
                elif is_text(c, "plain"):
                    out.append(c.viewable_text(html=False))

            for k in c.kids:
                app(k)

        if self.root is not None:
            app(self.root)
        return "".join(out)

    def refresh_tags(self, db: Db) -> None:
        def doit(m):
            if m is not None:
                self.tags = sorted(str(t) for t in m.tags)
                self.in_notmuch = True
            else:
                self.in_notmuch = False
        db.on_message(self.mid, doit)


class MessageThread:
    def __init__(self, thread_summary=None):
        self.thread = thread_summary
        self.messages: list[Message] = []
        self.subject = thread_summary.subject if thread_summary else ""

    def load_messages(self, db: Db) -> None:
        """Load all messages of the thread in date order with reply levels."""
        if self.thread is None:
            return

        entries: list[tuple[str, str, int, list[str], int]] = []

        def collect(t):
            if t is None:
                return

            def rec(msgs, level):
                for m in msgs:
                    entries.append((m.messageid, str(m.path), int(m.date),
                                    sorted(str(x) for x in m.tags), level))
                    rec(m.replies(), level + 1)

            rec(t.toplevel(), 0)

        db.on_thread(self.thread.thread_id, collect)

        self.messages = []
        for mid, path, date, tags, level in entries:
            msg = Message(filename=path, mid=mid)
            msg.tags = tags
            msg.time = date
            msg.level = level
            msg.in_notmuch = True
            self.messages.append(msg)

        if not self.subject and self.messages:
            self.subject = self.messages[0].subject
