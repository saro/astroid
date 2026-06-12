"""Tests for the action manager + tag actions against a real notmuch DB."""

import threading
import time

import pytest

notmuch2 = pytest.importorskip("notmuch2")

from astroid_mail.config import Config  # noqa: E402
from astroid_mail.db import Db, ThreadSummary  # noqa: E402
from astroid_mail.actions import (  # noqa: E402
    ActionManager, TagAction, ToggleAction, DiffTagAction)


@pytest.fixture()
def db_env(notmuch_db, config_env, monkeypatch):
    maildir, nm_config = notmuch_db
    monkeypatch.setenv("NOTMUCH_CONFIG", str(nm_config))
    cfg = Config()
    Db.init(cfg)
    yield maildir
    Db.path_db = None
    Db.excluded_tags = []


@pytest.fixture()
def manager(db_env):
    """ActionManager with a synchronous-ish dispatch we can pump manually."""
    pending = []
    am = ActionManager(dispatch=lambda fn: pending.append(fn))
    yield am, pending
    am.close()


def wait_idle(am, pending, timeout=10.0):
    """Wait until worker drained the queue, then run dispatched emitters."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        with am._cv:
            empty = not am._actions
        if empty and pending:
            break
        time.sleep(0.02)
    # give the worker a moment to finish the current action and dispatch
    time.sleep(0.1)
    while pending:
        fn = pending.pop(0)
        fn()


def first_thread() -> ThreadSummary:
    with Db(Db.READ_ONLY) as db:
        return ThreadSummary.from_notmuch(next(iter(db.threads("*"))))


def test_tag_action_with_signal(manager):
    am, pending = manager
    ts = first_thread()

    got = []
    am.connect("thread-updated", lambda _am, db, tid: got.append(("u", tid)))
    am.connect("thread-changed", lambda _am, db, tid: got.append(("c", tid)))

    am.doit(TagAction(ts, add=["pytag"]))
    wait_idle(am, pending)

    assert ("u", ts.thread_id) in got
    assert ("c", ts.thread_id) in got
    assert "pytag" in ts.tags

    fresh = first_thread()
    assert "pytag" in fresh.tags

    # cleanup
    am.doit(TagAction(ts, remove=["pytag"]))
    wait_idle(am, pending)


def test_undo_tag_action(manager):
    am, pending = manager
    ts = first_thread()

    am.doit(TagAction(ts, add=["undome"]))
    wait_idle(am, pending)
    assert "undome" in first_thread().tags

    am.undo()
    wait_idle(am, pending)
    assert "undome" not in first_thread().tags


def test_toggle_action_and_undo(manager):
    am, pending = manager
    ts = first_thread()
    had = "flagged" in ts.tags

    am.doit(ToggleAction(ts, "flagged"))
    wait_idle(am, pending)
    assert ("flagged" in first_thread().tags) == (not had)

    am.undo()
    wait_idle(am, pending)
    assert ("flagged" in first_thread().tags) == had


def test_difftag_parse():
    ts = ThreadSummary(thread_id="x", tags=["inbox", "unread"])

    a = DiffTagAction.create([ts], "+work -inbox plain")
    assert a is not None
    # plain counts as add; inbox is present -> removed; work/plain not present
    acts = a.taggable_actions
    assert len(acts) == 1
    _t, add, rem = acts[0]
    assert sorted(add) == ["plain", "work"]
    assert rem == ["inbox"]

    assert DiffTagAction.create([ts], "a, b") is None
    assert DiffTagAction.create([ts], "   ") is None


def test_difftag_apply_and_undo(manager):
    am, pending = manager
    ts = first_thread()
    assert "inbox" in ts.tags

    a = DiffTagAction.create([ts], "+difftag -inbox")
    am.doit(a)
    wait_idle(am, pending)
    fresh = first_thread()
    assert "difftag" in fresh.tags and "inbox" not in fresh.tags

    am.undo()
    wait_idle(am, pending)
    fresh = first_thread()
    assert "difftag" not in fresh.tags and "inbox" in fresh.tags


def test_skip_undo(manager):
    am, pending = manager
    ts = first_thread()

    am.doit(TagAction(ts, add=["noundo"]), undoable=False)
    wait_idle(am, pending)
    assert "noundo" in first_thread().tags

    am.undo()  # nothing on the undo stack
    wait_idle(am, pending)
    assert "noundo" in first_thread().tags

    am.doit(TagAction(ts, remove=["noundo"]), undoable=False)
    wait_idle(am, pending)
