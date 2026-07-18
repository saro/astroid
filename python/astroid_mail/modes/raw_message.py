"""Port of src/modes/raw_message.cc — non-editable monospace TextView."""

from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Pango, Gtk  # noqa: E402

from ..log import log
from .mode import Mode


class RawMessage(Mode):
    def __init__(self, main_window, *, content: str | bytes = "",
                 from_file: Path | None = None, delete_on_close: bool = False,
                 label: str = "Raw"):
        super().__init__(main_window)
        self.set_label(label)
        self.from_file = Path(from_file) if from_file else None
        self.delete_on_close = delete_on_close

        self.tv = Gtk.TextView()
        self.tv.set_editable(False)
        self.tv.set_monospace(True)
        self.tv.set_cursor_visible(False)
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_family_new("monospace"))

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.tv)
        scroll.set_vexpand(True)
        self.append(scroll)

        body = content
        if from_file is not None:
            try:
                body = self.from_file.read_bytes()
            except OSError as e:
                log.error("raw: could not read %s: %s", self.from_file, e)
                body = b""

        if isinstance(body, bytes):
            body = body.decode("utf-8", errors="replace")
        self.tv.get_buffer().set_text(body)

        self.scroll = scroll
        self._register_keys()

    def pre_close(self) -> None:
        if self.delete_on_close and self.from_file is not None:
            try:
                if self.from_file.is_file():
                    self.from_file.unlink()
            except OSError as e:
                log.warning("raw: could not unlink %s: %s", self.from_file, e)

    @classmethod
    def from_message(cls, main_window, msg) -> "RawMessage":
        return cls(main_window, content=msg.raw_contents(),
                   label=msg.subject or msg.mid)

    # -- keys ---------------------------------------------------------------------

    def _scroll(self, delta: int) -> bool:
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_value() + delta)
        return True

    def _register_keys(self) -> None:
        k = self.keys
        k.title = "Raw message"
        k.register_key("j", "raw.down", "Move down",
                       lambda _k: self._scroll(35), aliases=["Down"])
        k.register_key("k", "raw.up", "Move up",
                       lambda _k: self._scroll(-35), aliases=["Up"])
        k.register_key("J", "raw.page_down", "Page down",
                       lambda _k: self._scroll(400),
                       aliases=["Page_Down", "space"])
        k.register_key("K", "raw.page_up", "Page up",
                       lambda _k: self._scroll(-400),
                       aliases=["Page_Up", "S-space"])
        k.register_key("1", "raw.home", "Scroll to top",
                       lambda _k: (self.scroll.get_vadjustment().set_value(0),
                                   True)[1],
                       aliases=["Home"])
        k.register_key("0", "raw.end", "Scroll to bottom",
                       lambda _k: (self.scroll.get_vadjustment().set_value(
                           self.scroll.get_vadjustment().get_upper()), True)[1],
                       aliases=["End"])
