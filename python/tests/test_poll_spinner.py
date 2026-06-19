"""The poll spinner shows while poll.sh runs and hides when it finishes."""

from __future__ import annotations

import stat
import time
from pathlib import Path

import pytest

notmuch2 = pytest.importorskip("notmuch2")

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from astroid_mail.actions import ActionManager  # noqa: E402
from astroid_mail.config import Config  # noqa: E402
from astroid_mail.db import Db  # noqa: E402
from astroid_mail.poll import Poll  # noqa: E402


@pytest.fixture()
def env(notmuch_db, config_env, monkeypatch):
    maildir, nm_config = notmuch_db
    monkeypatch.setenv("NOTMUCH_CONFIG", str(nm_config))
    cfg = Config()
    Db.init(cfg)
    yield cfg, maildir, nm_config
    Db.path_db = None
    Db.excluded_tags = []


def _slow_poll_script(cfg, seconds: float) -> None:
    script = cfg.std_paths.config_dir / "poll.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f"#!/bin/sh\nsleep {seconds}\necho done\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


def test_spinner_visible_during_poll(env):
    import os
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        pytest.skip("no display (run under xvfb)")

    cfg, maildir, nm_config = env
    _slow_poll_script(cfg, 1.0)

    # minimal app stub the MainWindow needs
    class _App:
        pass

    app = Gtk.Application(application_id="org.astroid.spinnertest")

    captured = {"during": None, "after": None}

    def on_activate(a):
        from astroid_mail.main_window import MainWindow
        a.config = cfg
        a.accounts = None
        a.actions = ActionManager()
        a.poll = Poll(cfg, a.actions, auto_polling_enabled=False,
                      enable_timer=False)
        win = MainWindow(a)
        win.present()

        assert not win.poll_spinner.get_visible()
        a.poll.poll()

        def mid_check():
            captured["during"] = win.poll_spinner.get_visible()
            return False
        GLib.timeout_add(300, mid_check)

        def end_check():
            captured["after"] = win.poll_spinner.get_visible()
            a.actions.close()
            a.quit()
            return False
        GLib.timeout_add_seconds(3, end_check)

    app.connect("activate", on_activate)
    app.run([])

    assert captured["during"] is True, "spinner not visible during poll"
    assert captured["after"] is False, "spinner still visible after poll"
