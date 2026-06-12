"""Port of src/modes/mode.hh — base class for all notebook tabs."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..keybindings import Keybindings  # noqa: E402


class Mode(Gtk.Box):
    def __init__(self, main_window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.main_window = main_window
        self.keys = Keybindings()
        self.invincible = False
        self.label = ""

    def set_label(self, label: str) -> None:
        # tab labels are elided in the middle like the C++ (max 35 chars)
        if len(label) > 35:
            label = label[:15] + "..." + label[-15:]
        self.label = label
        if self.main_window is not None:
            self.main_window.update_tab_label(self, label)

    def get_label(self) -> str:
        return self.label

    def get_keys(self) -> Keybindings:
        return self.keys

    def grab_modal(self) -> None:
        self.grab_focus()

    def release_modal(self) -> None:
        pass

    def pre_close(self) -> None:
        pass

    def ask_yes_no(self, question: str, closure) -> None:
        self.main_window.ask_yes_no(question, closure)

    def multi_key(self, keybindings: Keybindings) -> None:
        self.main_window.enable_multi_key(keybindings)
