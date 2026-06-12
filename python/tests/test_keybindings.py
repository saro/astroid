"""Port of tests/test_keybindings.cc, using the same fixture file
(tests/test_home/keybindings) as the C++ suite."""

from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk  # noqa: E402

from astroid_mail.keybindings import (  # noqa: E402
    DuplicateKeyError, Key, KeySpecError, Keybindings, UnboundKey)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CPP_TEST_HOME = REPO_ROOT / "tests" / "test_home"


@pytest.fixture()
def user_bindings():
    Keybindings.reset_user_bindings()
    Keybindings.init(CPP_TEST_HOME)
    yield
    Keybindings.reset_user_bindings()


@pytest.fixture()
def no_user_bindings():
    Keybindings.reset_user_bindings()
    Keybindings._user_bindings_loaded = True  # block loading
    yield
    Keybindings.reset_user_bindings()


def press(keys: Keybindings, spec: str) -> bool:
    k = Key.from_spec(spec)
    state = Gdk.ModifierType(0)
    if k.ctrl:
        state |= Gdk.ModifierType.CONTROL_MASK
    if k.meta:
        state |= Gdk.ModifierType.ALT_MASK
    return keys.handle(k.key, state)


# -- Key spec parsing ---------------------------------------------------------

def test_key_spec_basic():
    k = Key.from_spec("j")
    assert not k.ctrl and not k.meta
    assert k.key == Gdk.unicode_to_keyval(ord("j"))
    assert k.spec() == "j"


def test_key_spec_modifiers():
    k = Key.from_spec("C-j")
    assert k.ctrl and not k.meta
    assert k.spec() == "C-j"

    k = Key.from_spec("M-x")
    assert k.meta and not k.ctrl

    k = Key.from_spec("C-M-k")
    assert k.ctrl and k.meta
    assert k.spec() == "C-M-k"


def test_key_spec_named_keys():
    k = Key.from_spec("Down")
    assert k.key == Gdk.KEY_Down
    assert k.spec() == "Down"

    k = Key.from_spec("C-Tab")
    assert k.ctrl
    assert k.key == Gdk.KEY_Tab


def test_key_spec_invalid_modifier():
    with pytest.raises(KeySpecError):
        Key.from_spec("1-a")
    with pytest.raises(KeySpecError):
        Key.from_spec("C-C-a")
    with pytest.raises(KeySpecError):
        Key.from_spec("C-M-C-a")


# -- registration + user override semantics (test_keybindings.cc) -------------

def test_user_override_rebinds(user_bindings):
    keys = Keybindings(title="Test")
    hit = []
    # default 'a', but file has 'test.a = k'
    keys.register_key("a", "test.a", "A", lambda k: hit.append(1) or True)

    assert not press(keys, "a")
    assert press(keys, "k")
    assert hit == [1]


def test_duplicate_user_alias_raises(user_bindings):
    keys = Keybindings(title="Test")
    # file: test.b=j appears twice with the same key -> conflict
    with pytest.raises(DuplicateKeyError):
        keys.register_key("b", "test.b", "B", lambda k: True)


def test_unbound_key_gets_bound_by_user(user_bindings):
    keys = Keybindings(title="Test")
    keys.register_key(UnboundKey(), "test.unbound", "U1", lambda k: True)
    keys.register_key(UnboundKey(), "test.unbound2", "U2", lambda k: True)

    # test.unbound2=1 in file
    assert press(keys, "1")
    # test.unbound has no user binding -> not registered
    assert not press(keys, "u")


def test_user_unbinds_default(user_bindings):
    keys = Keybindings(title="Test")

    def boom(k):
        raise AssertionError("should not run, unbound in keybindings file")

    # file: test.to_be_unbound=
    keys.register_key("7", "test.to_be_unbound", "U2", boom)
    assert not press(keys, "7")  # no handler, no exception


def test_duplicate_name_raises(user_bindings):
    keys = Keybindings(title="Test")
    keys.register_key("a", "test.a", "A", lambda k: True)
    with pytest.raises(DuplicateKeyError):
        keys.register_key("a", "test.a", "duplicate", lambda k: True)


def test_userdefined_key_beats_default(user_bindings):
    keys = Keybindings(title="Test")
    foo_hits, bar_hits = [], []

    # file: test.foo=0. Register test.foo (-> rebound to 0), then test.bar
    # with default key 0: the default loses, silently.
    keys.register_key("1", "test.foo", "some dup", lambda k: foo_hits.append(1) or True)
    keys.register_key("0", "test.bar", "some dup", lambda k: bar_hits.append(1) or True)

    assert press(keys, "0")
    assert foo_hits == [1] and bar_hits == []

    # and the other way around: default first, then user-rebound target
    keys.register_key("2", "test.bar2", "some dup", lambda k: bar_hits.append(2) or True)
    keys.register_key("3", "test.foo2", "some dup", lambda k: foo_hits.append(2) or True)
    # file: test.foo2=2 -> overwrites the default binding of test.bar2
    assert press(keys, "2")
    assert foo_hits == [1, 2] and bar_hits == []


def test_run_hooks(user_bindings):
    keys = Keybindings(title="Test")
    got = []

    keys.register_run("test.run",
                      lambda k, cmd, undo: got.append((k.spec(), cmd, undo)) or True)

    assert press(keys, "n")
    assert got[-1] == ("n", "echo %1", "")

    assert press(keys, "y")
    assert got[-1] == ("y", "echo %1", "")

    assert press(keys, "4")
    assert got[-1] == ("4", "echo %1", "echo undo %1")

    assert press(keys, "5")
    assert got[-1] == ("5", "echo %1", "echo undo %1")

    # escaped comma in command, no undo
    assert press(keys, "6")
    assert got[-1] == ("6", "echo %1, no undo", "")

    # escaped comma in command + undo
    assert press(keys, "7")
    assert got[-1] == ("7", "echo %1, no undo", "echo real undo %1")


def test_aliases(user_bindings):
    keys = Keybindings(title="Test")
    hits = []
    # file: thread_index.next=J + =k (alias)
    keys.register_key("j", "thread_index.next", "next", lambda k: hits.append(k.spec()) or True)

    assert not press(keys, "j")   # default dropped by user rebind
    assert press(keys, "J")
    assert press(keys, "k")
    assert hits == ["J", "k"]


def test_special_key_specs(user_bindings):
    keys = Keybindings(title="Test")
    hits = []
    keys.register_key(UnboundKey(), "test.spec1", "s1", lambda k: hits.append(1) or True)
    keys.register_key(UnboundKey(), "test.spec2", "s2", lambda k: hits.append(2) or True)

    assert press(keys, "Tab")
    assert press(keys, "C-Tab")
    assert hits == [1, 2]


# -- without user bindings ----------------------------------------------------

def test_default_registration_and_aliases(no_user_bindings):
    keys = Keybindings(title="Test")
    hits = []
    keys.register_key("j", "t.down", "Down", lambda k: hits.append(k.spec()) or True,
                      aliases=["Down"])
    assert press(keys, "j")
    assert press(keys, "Down")
    assert hits == ["j", "Down"]


def test_hardcoded_conflict_raises(no_user_bindings):
    keys = Keybindings(title="Test")
    keys.register_key("x", "t.x", "X", lambda k: True)
    with pytest.raises(DuplicateKeyError):
        keys.register_key("x", "t.y", "Y", lambda k: True)


def test_help_output(no_user_bindings):
    keys = Keybindings(title="Test")
    keys.register_key("j", "t.down", "Move down", lambda k: True, aliases=["Down"])
    h = keys.help()
    assert "<b>j,Down</b>: Move down" in h
    sh = keys.short_help()
    assert sh.startswith("<b>Test</b>: ")
    assert "j: Move down" in sh


def test_handle_name(no_user_bindings):
    keys = Keybindings(title="Test")
    hits = []
    keys.register_key("j", "t.down", "Down", lambda k: hits.append(1) or True)
    assert keys.handle_name("t.down")
    assert hits == [1]
    with pytest.raises(KeySpecError):
        keys.handle_name("t.nope")
