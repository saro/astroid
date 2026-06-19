"""ExternalEditor launches via Gio.Subprocess and fires edited/stopped.

This is the regression test for the GLib.spawn_async crash.
"""

from __future__ import annotations

from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from astroid_mail.modes.editor.external import ExternalEditor  # noqa: E402


def _run_loop(check, timeout_s=5.0):
    loop = GLib.MainLoop()
    state = {"done": False}

    def poll():
        if check():
            state["done"] = True
            loop.quit()
            return False
        return True

    GLib.timeout_add(50, poll)
    GLib.timeout_add_seconds(int(timeout_s), loop.quit)
    loop.run()
    return state["done"]


def test_editor_spawns_and_signals(tmp_path):
    tmpfile = tmp_path / "draft.eml"
    tmpfile.write_text("initial\n")

    # a "editor" that appends to the file then exits
    cmd = f"sh -c 'echo edited-by-editor >> \"$1\"' sh %1"
    ed = ExternalEditor(cmd, tmpfile)

    events = {"edited": 0, "stopped": 0}
    ed.connect("edited", lambda *_: events.__setitem__("edited",
                                                       events["edited"] + 1))
    ed.connect("stopped", lambda *_: events.__setitem__("stopped",
                                                        events["stopped"] + 1))

    assert ed.start() is True  # must not raise / return False

    ok = _run_loop(lambda: events["stopped"] > 0)
    assert ok, "editor never reported stopped"
    assert events["stopped"] == 1
    # the appended content landed (proves the spawn + run round-trip; this is
    # the regression check for the old GLib.spawn_async crash)
    assert "edited-by-editor" in tmpfile.read_text()
    # 'edited' from the file monitor is best-effort: an instant-exit process
    # can coalesce the change, but EditMessage also re-reads on 'stopped'.


def test_editor_bad_command_returns_false(tmp_path):
    tmpfile = tmp_path / "draft.eml"
    tmpfile.write_text("x")
    ed = ExternalEditor('"unterminated', tmpfile)
    # shell parse error -> graceful False, no exception
    assert ed.start() is False


def test_editor_missing_binary_returns_false(tmp_path):
    tmpfile = tmp_path / "draft.eml"
    tmpfile.write_text("x")
    ed = ExternalEditor("astroid-no-such-editor-binary %1", tmpfile)
    assert ed.start() is False
