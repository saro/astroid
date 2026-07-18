"""space / S-space bindings: shift handling in the Key model and page
scrolling wired in the modes."""

from __future__ import annotations

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk  # noqa: E402

from astroid_mail.keybindings import Key, Keybindings, KeySpecError  # noqa: E402


@pytest.fixture()
def no_user_bindings():
    Keybindings.reset_user_bindings()
    Keybindings._user_bindings_loaded = True
    yield
    Keybindings.reset_user_bindings()


def test_spec_parses_shift_modifier():
    k = Key.from_spec("S-space")
    assert k.shift and not k.ctrl and not k.meta
    assert k.key == Gdk.KEY_space
    assert k.spec() == "S-space"

    k = Key.from_spec("C-S-space")
    assert k.ctrl and k.shift
    assert k.spec() == "C-S-space"

    with pytest.raises(KeySpecError):
        Key.from_spec("S-S-space")


def test_event_shift_space_matches_s_space():
    bound = Key.from_spec("S-space")
    ev = Key.from_event(Gdk.KEY_space, Gdk.ModifierType.SHIFT_MASK)
    assert ev._id() == bound._id()

    plain = Key.from_event(Gdk.KEY_space, Gdk.ModifierType(0))
    assert plain._id() == Key.from_spec("space")._id()
    assert plain._id() != bound._id()


def test_event_shift_letters_not_flagged():
    # Shift+x arrives as keyval X: shift is consumed by the case change and
    # must NOT be recorded, so 'X' bindings keep matching.
    ev = Key.from_event(Gdk.KEY_X, Gdk.ModifierType.SHIFT_MASK)
    assert ev.shift is False
    assert ev._id() == Key.from_spec("X")._id()


def test_event_iso_left_tab_not_flagged():
    # Shift+Tab arrives as ISO_Left_Tab which already encodes the shift
    ev = Key.from_event(Gdk.KEY_ISO_Left_Tab, Gdk.ModifierType.SHIFT_MASK)
    assert ev.shift is False


def test_space_bindings_dispatch(no_user_bindings):
    keys = Keybindings(title="t")
    hits = []
    keys.register_key("J", "t.page_down", "down",
                      lambda k: hits.append("down") or True,
                      aliases=["space"])
    keys.register_key("K", "t.page_up", "up",
                      lambda k: hits.append("up") or True,
                      aliases=["S-space"])

    assert keys.handle(Gdk.KEY_space, Gdk.ModifierType(0))
    assert keys.handle(Gdk.KEY_space, Gdk.ModifierType.SHIFT_MASK)
    assert hits == ["down", "up"]


def test_modes_bind_space_first_class(no_user_bindings):
    """The four scrolling modes bind space/S-space as first-class named
    bindings (spacebar_down/spacebar_up), NOT as aliases of page_down/up —
    a user override of <mode>.page_down must not drop the space keys."""
    import inspect
    from astroid_mail.modes.thread_view import thread_view
    from astroid_mail.modes.thread_index import thread_index
    from astroid_mail.modes import edit_message, raw_message

    for mod in (thread_view, thread_index, edit_message, raw_message):
        src = inspect.getsource(mod)
        assert '"space"' in src, mod.__name__
        assert '"S-space"' in src, mod.__name__
        assert "spacebar_down" in src, mod.__name__
        assert "spacebar_up" in src, mod.__name__


def test_space_survives_page_down_user_override(no_user_bindings):
    """A user keybindings line overriding page_down replaces its key and
    drops its aliases (C++ semantics) — space must keep working because it
    is registered under its own name."""
    keys = Keybindings(title="t")
    Keybindings.user_bindings = [
        # user rebinds t.page_down to 'd': default key J and aliases dropped
        _user_key("t.page_down", "d"),
    ]
    hits = []
    keys.register_key("J", "t.page_down", "down",
                      lambda k: hits.append("pd") or True,
                      aliases=["Page_Down"])
    keys.register_key("space", "t.spacebar_down", "down (spacebar)",
                      lambda k: hits.append("space") or True)

    # the old alias route would be dead now; the named binding still fires
    assert keys.handle(Gdk.KEY_space, Gdk.ModifierType(0))
    assert hits == ["space"]
    # and the user's chosen key works for page_down
    assert keys.handle(Gdk.KEY_d, Gdk.ModifierType(0))
    assert hits == ["space", "pd"]


def _user_key(name, spec):
    k = Key.from_spec(spec)
    k.name = name
    k.userdefined = True
    return k
