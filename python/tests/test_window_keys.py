"""Verify MainWindow defers to typing widgets only when appropriate.

This exercises ``_focus_is_typing_widget`` directly (the gate used by
the window-level key controller) so we don't need a real display.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"),
    reason="no display (run under xvfb)")

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from astroid_mail.main_window import MainWindow  # noqa: E402


class _Stub:
    def __init__(self, focus_widget=None):
        self._focus = focus_widget

    def get_focus(self):
        return self._focus

    # bind the real instance methods to this stub
    _is_typing_widget = staticmethod(MainWindow._is_typing_widget)
    _focus_is_typing_widget = MainWindow._focus_is_typing_widget


def _ev(keyval, mods=0):
    return keyval, Gdk.ModifierType(mods)


def test_plain_letter_in_entry_is_typing():
    s = _Stub(focus_widget=Gtk.Entry())
    assert s._focus_is_typing_widget(*_ev(Gdk.KEY_x)) is True
    assert s._focus_is_typing_widget(*_ev(Gdk.KEY_a)) is True
    # space too: an Entry should consume it
    assert s._focus_is_typing_widget(*_ev(Gdk.KEY_space)) is True


def test_escape_return_tab_are_commands_in_entry():
    s = _Stub(focus_widget=Gtk.Entry())
    assert s._focus_is_typing_widget(*_ev(Gdk.KEY_Escape)) is False
    assert s._focus_is_typing_widget(*_ev(Gdk.KEY_Return)) is False
    assert s._focus_is_typing_widget(*_ev(Gdk.KEY_Tab)) is False


def test_modifier_combos_always_reach_commands():
    s = _Stub(focus_widget=Gtk.Entry())
    assert s._focus_is_typing_widget(
        *_ev(Gdk.KEY_x, Gdk.ModifierType.CONTROL_MASK)) is False
    assert s._focus_is_typing_widget(
        *_ev(Gdk.KEY_x, Gdk.ModifierType.ALT_MASK)) is False


def test_no_focused_widget_means_no_typing():
    s = _Stub(focus_widget=None)
    assert s._focus_is_typing_widget(*_ev(Gdk.KEY_x)) is False


def test_textview_counts_as_typing():
    s = _Stub(focus_widget=Gtk.TextView())
    assert s._focus_is_typing_widget(*_ev(Gdk.KEY_x)) is True


def test_non_text_widget_does_not_swallow():
    s = _Stub(focus_widget=Gtk.Button(label="click"))
    assert s._focus_is_typing_widget(*_ev(Gdk.KEY_x)) is False
