"""MainWindow key dispatch: bound keys are consumed (and never reach the
focused widget); unbound keys fall through so they can be typed.

Matches the C++ MainWindow::on_key_press model. Pure stub - no display.
"""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk  # noqa: E402

from astroid_mail.keybindings import Keybindings  # noqa: E402
from astroid_mail.main_window import MainWindow  # noqa: E402


class _CmdBar:
    def __init__(self, searching=False):
        self._s = searching

    def get_search_mode(self):
        return self._s


class _Win:
    """Minimal stand-in exercising MainWindow._on_key_pressed."""
    _on_key_pressed = MainWindow._on_key_pressed

    def __init__(self, mode_keys=None, win_keys=None, searching=False):
        self._yes_no_waiting = False
        self._multi_waiting = False
        self.command = _CmdBar(searching)
        self._mode = type("M", (), {"get_keys": lambda s: mode_keys})() \
            if mode_keys is not None else None
        self.keys = win_keys

    def current_mode(self):
        return self._mode


def _kb(name, spec):
    kb = Keybindings(title="t")
    hits = {"n": 0}
    kb.register_key(spec, name, name,
                    lambda _k: (hits.__setitem__("n", hits["n"] + 1), True)[1])
    return kb, hits


def test_bound_window_key_is_consumed():
    win_keys, hits = _kb("main_window.close_page", "x")
    w = _Win(win_keys=win_keys)
    handled = w._on_key_pressed(None, Gdk.KEY_x, 0, Gdk.ModifierType(0))
    assert handled is True
    assert hits["n"] == 1


def test_unbound_key_falls_through():
    win_keys, hits = _kb("main_window.close_page", "x")
    w = _Win(win_keys=win_keys)
    handled = w._on_key_pressed(None, Gdk.KEY_q, 0, Gdk.ModifierType(0))
    assert handled is False  # 'q' not bound -> propagate to focused widget
    assert hits["n"] == 0


def test_mode_key_takes_priority_over_window():
    win_keys, win_hits = _kb("main_window.close_page", "x")
    mode_keys, mode_hits = _kb("edit_message.delete_draft", "D")
    w = _Win(mode_keys=mode_keys, win_keys=win_keys)

    assert w._on_key_pressed(None, Gdk.KEY_D, 0, Gdk.ModifierType(0)) is True
    assert mode_hits["n"] == 1
    # window 'x' still works (handled by window keys)
    assert w._on_key_pressed(None, Gdk.KEY_x, 0, Gdk.ModifierType(0)) is True
    assert win_hits["n"] == 1


def test_command_bar_search_mode_lets_text_through():
    win_keys, hits = _kb("main_window.close_page", "x")
    w = _Win(win_keys=win_keys, searching=True)
    # while the command bar is open, even a bound key is not consumed here
    assert w._on_key_pressed(None, Gdk.KEY_x, 0, Gdk.ModifierType(0)) is False
    assert hits["n"] == 0


def test_yes_no_prompt_consumes_y_n():
    win_keys, _ = _kb("main_window.close_page", "x")
    w = _Win(win_keys=win_keys)
    w._yes_no_waiting = True
    answered = []
    w.answer_yes_no = lambda yes: answered.append(yes)
    assert w._on_key_pressed(None, Gdk.KEY_y, 0, Gdk.ModifierType(0)) is True
    assert answered == [True]
