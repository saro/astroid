"""Port of src/chunk.cc — a MIME part tree with viewable/attachment flags.

Viewable types and preference selection follow the C++ rules:
* text/plain, text/html are viewable; the part matching
  thread_view.preferred_type is marked preferred
* multipart/alternative children become siblings of each other
* leaf parts with a content-disposition of attachment (or un-viewable
  types) are attachments
"""

from __future__ import annotations

import html as html_mod
import re

import gi

gi.require_version("GMime", "3.0")
from gi.repository import GMime  # noqa: E402

from ..log import log  # noqa: E402

GMime.init()

_id_counter = 0


def _next_id() -> int:
    global _id_counter
    _id_counter += 1
    return _id_counter


VIEWABLE_TYPES = ("text/plain", "text/html")

URL_RE = re.compile(
    r"(https?://[^\s<>\"]+|ftp://[^\s<>\"]+|mailto:[^\s<>\"]+|"
    r"www\.[^\s<>\"]+)")


def text_to_html(text: str) -> str:
    """Pure-python replacement for the C++ gmime-filter-html-bq filter:
    escape, linkify, mark quote levels with blockquotes, convert newlines."""
    out = []
    open_levels = 0
    for line in text.split("\n"):
        level = 0
        rest = line
        while rest.startswith(">"):
            level += 1
            rest = rest[1:]
            if rest.startswith(" "):
                rest = rest[1:]

        while open_levels < level:
            out.append('<blockquote class="level_%d">' % (open_levels + 1))
            open_levels += 1
        while open_levels > level:
            out.append("</blockquote>")
            open_levels -= 1

        esc = html_mod.escape(rest, quote=False)
        esc = URL_RE.sub(
            lambda m: '<a href="%s">%s</a>' % (
                m.group(0) if "://" in m.group(0) or m.group(0).startswith("mailto:")
                else "http://" + m.group(0),
                m.group(0)),
            esc)
        out.append(esc + "<br>")

    while open_levels > 0:
        out.append("</blockquote>")
        open_levels -= 1

    return "\n".join(out)


class Chunk:
    def __init__(self, mime_object, preferred_type: str = "plain"):
        self.id = _next_id()
        self.mime_object = mime_object
        self.preferred_type = preferred_type

        self.kids: list[Chunk] = []
        self.siblings: list[Chunk] = []

        self.viewable = False
        self.preferred = False
        self.attachment = False
        self.mime_message = False

        self.issigned = False
        self.isencrypted = False
        self.crypt = None

        self.content_id = ""
        self.content_type = None
        self.mime_type = "application/octet-stream"

        if mime_object is None:
            return

        ct = mime_object.get_content_type()
        if ct is not None:
            self.content_type = ct
            self.mime_type = ct.get_mime_type().lower()

        cid = None
        if isinstance(mime_object, GMime.Part):
            cid = mime_object.get_content_id()
        self.content_id = cid or ""

        if isinstance(mime_object, GMime.Multipart):
            self._parse_multipart(mime_object)
        elif isinstance(mime_object, GMime.MessagePart):
            self._parse_message_part(mime_object)
        elif isinstance(mime_object, GMime.Part):
            self._parse_part(mime_object)

    # -- parsing -----------------------------------------------------------

    def _parse_multipart(self, mp: GMime.Multipart) -> None:
        subtype = (self.content_type.get_media_subtype() or "").lower() \
            if self.content_type else ""

        kids = [Chunk(mp.get_part(i), self.preferred_type)
                for i in range(mp.get_count())]

        if subtype == "alternative":
            # children are siblings; pick the preferred one
            for k in kids:
                k.siblings = [o for o in kids if o is not k]
            want = f"text/{self.preferred_type}"
            chosen = None
            for k in kids:
                if k.mime_type == want:
                    chosen = k
            if chosen is None and kids:
                chosen = kids[-1]
            for k in kids:
                k.preferred = k is chosen
            self.kids = kids
        else:
            # encrypted/signed handled in Phase 3 (crypto); show kids
            self.kids = kids

    def _parse_message_part(self, mp: GMime.MessagePart) -> None:
        self.mime_message = True
        self.attachment = False

    def _parse_part(self, part: GMime.Part) -> None:
        disposition = (part.get_disposition() or "").lower()

        if self.mime_type in VIEWABLE_TYPES and disposition != "attachment":
            self.viewable = True
            if self.mime_type == f"text/{self.preferred_type}":
                self.preferred = True
            # without siblings a single viewable part is the one shown
            if not self.siblings:
                self.preferred = self.preferred or True
        else:
            self.attachment = True

    # -- content -----------------------------------------------------------

    def viewable_text(self, html: bool = True) -> str:
        """Decoded text of this part; optionally converted to display HTML."""
        if not isinstance(self.mime_object, (GMime.Part,)):
            return ""

        if isinstance(self.mime_object, GMime.TextPart):
            try:
                text = self.mime_object.get_text() or ""
            except UnicodeDecodeError:
                # broken charset declaration (see convert_error.eml fixture):
                # fall back to a lossy decode of the raw bytes
                text = self.contents().decode("utf-8", errors="replace")
        else:
            text = self.contents().decode("utf-8", errors="replace")

        if self.mime_type == "text/html":
            return text if html else text  # html-to-text via w3m in Phase 2

        if html:
            return text_to_html(text)
        return text

    def contents(self) -> bytes:
        """Decoded raw bytes of a leaf part."""
        if not isinstance(self.mime_object, GMime.Part):
            return b""
        wrapper = self.mime_object.get_content()
        if wrapper is None:
            return b""
        out = GMime.StreamMem.new()
        wrapper.write_to_stream(out)
        return bytes(out.get_byte_array())

    def get_filename(self) -> str:
        if isinstance(self.mime_object, GMime.Part):
            return self.mime_object.get_filename() or ""
        return ""

    def get_file_size(self) -> int:
        return len(self.contents())

    def save_to(self, path) -> None:
        with open(path, "wb") as f:
            f.write(self.contents())

    # -- traversal -----------------------------------------------------------

    def all_parts(self) -> list["Chunk"]:
        parts = [self]
        for k in self.kids:
            parts.extend(k.all_parts())
        return parts
