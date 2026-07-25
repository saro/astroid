"""Port of src/command_bar.cc (Phase 1: search + tag entry).

A Gtk.SearchBar with a mode label and an entry; completion comes later
(custom popover — Gtk.EntryCompletion is deprecated in GTK4).
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GObject, Gtk  # noqa: E402

from .log import log  # noqa: E402


class CommandBar(Gtk.SearchBar):
    MODE_SEARCH = "search"
    MODE_TAG = "tag"

    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.mode = self.MODE_SEARCH
        self._callback = None

        # SearchBar centers its child by default: expand the box across the
        # whole window width, with the entry taking all remaining space.
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_hexpand(True)
        box.set_margin_start(6)
        box.set_margin_end(6)
        self.mode_label = Gtk.Label()
        self.entry = Gtk.Entry()
        self.entry.set_hexpand(True)
        box.append(self.mode_label)
        box.append(self.entry)
        self.set_child(box)

        self.connect_entry(self.entry)
        self.entry.connect("activate", self._on_activate)

        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_key)
        self.entry.add_controller(controller)

    def enable_command(self, mode: str, title: str, initial: str,
                       callback) -> None:
        self.mode = mode
        self._callback = callback
        self.mode_label.set_text(title or {"search": "Search:",
                                           "tag": "Tags:"}.get(mode, mode))
        self.entry.set_text(initial or "")
        self.entry.set_position(-1)
        self.set_search_mode(True)
        self.entry.grab_focus()
        self.entry.select_region(0, 0)
        self.entry.set_position(len(initial or ""))

    def disable_command(self) -> None:
        self.set_search_mode(False)
        self._callback = None
        self.main_window.grab_active()

    def _on_activate(self, entry) -> None:
        text = entry.get_text()
        cb = self._callback
        self.disable_command()

        if cb is not None:
            cb(text)
        elif self.mode == self.MODE_SEARCH and text.strip():
            from .modes.thread_index.thread_index import ThreadIndex
            self.main_window.add_mode(
                ThreadIndex(self.main_window, text.strip()))

    def _on_key(self, controller, keyval, keycode, state) -> bool:
        import gi
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk
        if keyval == Gdk.KEY_Escape:
            self.disable_command()
            return True
        return False
