"""Regression: the thread list updates when poll brings in new mail."""

from __future__ import annotations

import shutil
import stat
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

notmuch2 = pytest.importorskip("notmuch2")

from gi.repository import Gio  # noqa: E402

from astroid_mail.actions import ActionManager  # noqa: E402
from astroid_mail.config import Config  # noqa: E402
from astroid_mail.db import Db  # noqa: E402
from astroid_mail.modes.thread_index.query_loader import (  # noqa: E402
    QueryLoader, ThreadItem,
)
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


def _drain(pending, until=lambda: False, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        while pending:
            fn, args = pending.pop(0) if isinstance(pending[0], tuple) else (
                pending.pop(0), ())
            fn(*args)
        if until():
            return True
        time.sleep(0.02)
    while pending:
        x = pending.pop(0)
        if isinstance(x, tuple):
            x[0](*x[1])
        else:
            x()
    return until()


def _write_poll_script(cfg, maildir, nm_config, eml, sleep_s=0):
    script = cfg.std_paths.config_dir / "poll.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    eml_path = cfg.std_paths.config_dir / "new.eml"
    eml_path.write_text(eml)
    sleep_line = f"sleep {sleep_s}\n" if sleep_s else ""
    script.write_text(
        "#!/bin/sh\n"
        f"{sleep_line}"
        f"cp '{eml_path}' '{maildir}/poll-arrived.eml'\n"
        f"NOTMUCH_CONFIG='{nm_config}' notmuch new\n"
        "echo poll done\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


NEW_INBOX_MAIL = """From: Polly <polly@example.com>
To: root@localhost
Subject: hello from polly
Message-Id: <poll-new-1@example.com>
Date: Thu, 01 Jan 2026 10:00:00 +0000

new arrival via poll
"""


def test_thread_list_updates_after_poll_under_main_loop(env):
    """The bug repro - using a real GLib main loop end-to-end."""
    from gi.repository import GLib

    cfg, maildir, nm_config = env

    am = ActionManager()  # default GLib.idle_add dispatch
    try:
        store = Gio.ListStore(item_type=ThreadItem)
        loader = QueryLoader(store)  # default GLib.idle_add
        am.connect("thread-changed",
                   lambda _am, db, tid: loader.on_thread_changed(db, tid))

        loader.start("tag:inbox")
        # spin the main loop until loader is done
        loop = GLib.MainLoop()
        def check_loaded():
            if not loader.loading:
                loop.quit()
                return False
            return True
        GLib.timeout_add(50, check_loaded)
        GLib.timeout_add_seconds(10, loop.quit)
        loop.run()
        initial = store.get_n_items()
        assert initial > 0

        # write poll script + drive a poll
        _write_poll_script(cfg, maildir, nm_config, NEW_INBOX_MAIL)
        poll = Poll(cfg, am, auto_polling_enabled=False, enable_timer=False)
        assert poll.poll()

        loop = GLib.MainLoop()
        def check_updated():
            if store.get_n_items() > initial:
                loop.quit()
                return False
            return True
        GLib.timeout_add(100, check_updated)
        GLib.timeout_add_seconds(15, loop.quit)
        loop.run()

        tids = [store.get_item(i).summary.thread_id
                for i in range(store.get_n_items())]
        with Db(Db.READ_ONLY) as db:
            new_tids = [t.threadid
                        for t in db.threads("id:poll-new-1@example.com")]
        assert new_tids, "poll didn't actually index the new mail"
        assert new_tids[0] in tids, \
            f"new thread {new_tids[0]!r} not in store; rows = {tids}"
    finally:
        am.close()


def test_thread_list_updates_after_poll(env):
    """Bug repro: poll inserts a new thread; the loader's store should
    contain a row for the new thread id once the signal has been emitted."""
    cfg, maildir, nm_config = env

    # action manager that we pump manually so we run on a deterministic
    # 'GUI thread' (the test thread)
    pending = []
    am = ActionManager(dispatch=lambda fn: pending.append(fn))

    # loader for the inbox
    store = Gio.ListStore(item_type=ThreadItem)
    loader = QueryLoader(store, dispatch=lambda fn: pending.append(fn))
    am.connect("thread-changed",
               lambda _am, db, tid: loader.on_thread_changed(db, tid))

    loader.start("tag:inbox")
    _drain(pending, until=lambda: not loader.loading)
    initial = store.get_n_items()
    assert initial > 0

    # poll: drop a new eml + notmuch new
    _write_poll_script(cfg, maildir, nm_config, NEW_INBOX_MAIL)
    poll = Poll(cfg, am, auto_polling_enabled=False,
                dispatch=lambda fn, *a: pending.append((fn, a)),
                enable_timer=False)
    poll.poll()

    # poll runs in a thread; we have to wait for _poll_done to be
    # dispatched
    _drain(pending, until=lambda: store.get_n_items() > initial, timeout=15.0)

    am.close()

    tids = [store.get_item(i).summary.thread_id
            for i in range(store.get_n_items())]
    # find the new mail's thread id via notmuch directly
    with Db(Db.READ_ONLY) as db:
        new_tids = [t.threadid
                    for t in db.threads("id:poll-new-1@example.com")]
    assert new_tids, "poll didn't actually index the new mail"
    new_tid = new_tids[0]

    assert new_tid in tids, \
        f"new thread {new_tid!r} not in store; rows = {tids}"
    assert store.get_n_items() == initial + 1
