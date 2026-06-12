"""Port of src/modes/help_mode.cc — show keybindings of the calling mode."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .mode import Mode  # noqa: E402


class HelpMode(Mode):
    def __init__(self, main_window, for_mode: Mode | None = None):
        super().__init__(main_window)
        self.set_label("Help")

        self.scroll = Gtk.ScrolledWindow()
        self.label = Gtk.Label(xalign=0, yalign=0)
        self.label.set_margin_start(12)
        self.label.set_margin_top(12)
        self.label.set_selectable(True)
        self.scroll.set_child(self.label)
        self.scroll.set_vexpand(True)
        self.append(self.scroll)

        sections = []
        if for_mode is not None:
            ks = for_mode.get_keys()
            sections.append((ks.title or for_mode.get_label(), ks.help()))
        if main_window is not None:
            sections.append(("Main window", main_window.keys.help()))

        markup = ""
        for title, body in sections:
            markup += f"<big><b>{title}</b></big>\n\n{body}\n"
        self.label.set_markup(markup)

        self.keys.title = "Help"
        self.keys.register_key("j", "help.down", "Scroll down",
                               lambda k: self._scroll(60), aliases=["Down"])
        self.keys.register_key("k", "help.up", "Scroll up",
                               lambda k: self._scroll(-60), aliases=["Up"])

    def _scroll(self, delta: int) -> bool:
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_value() + delta)
        return True
