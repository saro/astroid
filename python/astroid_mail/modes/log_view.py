"""Port of src/modes/log_view.cc — tails the astroid logger.

Attaches a logging.Handler to the 'astroid' logger and renders incoming
records as colored Pango markup in a Gtk.ListView. Records arriving
off-thread are marshalled via GLib.idle_add.
"""

from __future__ import annotations

import html
import logging
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject, Gtk  # noqa: E402

from ..log import log
from .mode import Mode

_COLOR = {
    logging.CRITICAL: "red",
    logging.ERROR: "red",
    logging.WARNING: "pink",
    logging.INFO: "gray",
    logging.DEBUG: "gray",
}


class _LogRow(GObject.Object):
    __gtype_name__ = "AstroidLogRow"
    markup = GObject.Property(type=str, default="")

    def __init__(self, markup: str):
        super().__init__()
        self.markup = markup


class _LogViewHandler(logging.Handler):
    def __init__(self, on_record):
        super().__init__()
        self.on_record = on_record

    def emit(self, record: logging.LogRecord) -> None:
        try:
            colour = _COLOR.get(record.levelno, "gray")
            ts = time.strftime("%H:%M:%S", time.localtime(record.created))
            text = self.format(record)
            line = (f'<span color="{colour}">'
                    f'<i>[{record.levelname.lower()}]</i> {ts}: '
                    f'{html.escape(text)}</span>')
            GLib.idle_add(self.on_record, line)
        except Exception:
            self.handleError(record)


class LogView(Mode):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.set_label("Log")
        self.invincible = False

        self.store = Gio.ListStore(item_type=_LogRow)
        self.selection = Gtk.SingleSelection(model=self.store)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)

        self.list_view = Gtk.ListView(model=self.selection, factory=factory)
        self.list_view.set_vexpand(True)

        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.list_view)
        scroll.set_vexpand(True)
        self.append(scroll)
        self.scroll = scroll

        self._handler = _LogViewHandler(self._on_record)
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(self._handler)

        self._register_keys()

    # -- factory -----------------------------------------------------------------

    def _on_setup(self, factory, item) -> None:
        lbl = Gtk.Label(xalign=0)
        lbl.set_use_markup(True)
        item.set_child(lbl)

    def _on_bind(self, factory, item) -> None:
        item.get_child().set_markup(item.get_item().markup)

    # -- log sink ----------------------------------------------------------------

    def _on_record(self, markup: str) -> bool:
        self.store.append(_LogRow(markup))
        # autoscroll if at the bottom
        adj = self.scroll.get_vadjustment()
        if adj.get_upper() - adj.get_value() - adj.get_page_size() < 5:
            adj.set_value(adj.get_upper())
        return False

    def pre_close(self) -> None:
        log.removeHandler(self._handler)

    # -- keys -----------------------------------------------------------------

    def _move(self, delta: int) -> bool:
        n = self.store.get_n_items()
        if n == 0:
            return True
        pos = self.selection.get_selected()
        if pos == Gtk.INVALID_LIST_POSITION:
            pos = 0
        pos = max(0, min(n - 1, pos + delta))
        self.selection.set_selected(pos)
        return True

    def _register_keys(self) -> None:
        k = self.keys
        k.title = "Log view"
        k.register_key("j", "log.down", "Down",
                       lambda _k: self._move(1), aliases=["Down"])
        k.register_key("k", "log.up", "Up",
                       lambda _k: self._move(-1), aliases=["Up"])
        k.register_key("J", "log.page_down", "Page down",
                       lambda _k: self._move(10), aliases=["Page_Down"])
        k.register_key("K", "log.page_up", "Page up",
                       lambda _k: self._move(-10), aliases=["Page_Up"])
        k.register_key("1", "log.home", "Scroll to top",
                       lambda _k: (self.selection.set_selected(0), True)[1],
                       aliases=["Home"])
        k.register_key("0", "log.end", "Scroll to bottom",
                       lambda _k: (self.selection.set_selected(
                           max(0, self.store.get_n_items() - 1)), True)[1],
                       aliases=["End"])
