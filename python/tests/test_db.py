"""Tests for db.py against a real notmuch database (ports of test_notmuch.cc
patterns plus the locking model)."""

import threading
import time

import pytest

notmuch2 = pytest.importorskip("notmuch2")

from astroid_mail.config import Config  # noqa: E402
from astroid_mail.db import (  # noqa: E402
    Db, DatabaseError, ThreadSummary, MessageSummary,
    thread_add_tag, thread_remove_tag)


@pytest.fixture()
def db_env(notmuch_db, config_env, monkeypatch):
    maildir, nm_config = notmuch_db
    # config_env clears NOTMUCH_CONFIG; restore it after both fixtures ran
    monkeypatch.setenv("NOTMUCH_CONFIG", str(nm_config))
    cfg = Config()
    Db.init(cfg)
    yield maildir
    # reset class state
    Db.path_db = None
    Db.excluded_tags = []
    Db.tags = []


def test_init_reads_notmuch_config(db_env):
    assert Db.path_db is not None
    assert Db.excluded_tags == ["deleted", "spam"]
    assert Db.maildir_synchronize_flags is True
    assert Db.sent_tags == ["sent"]


def test_init_requires_notmuch_config(config_env):
    cfg = Config()
    cfg.has_notmuch_config = False
    with pytest.raises(DatabaseError):
        Db.init(cfg)


def test_thread_summary(db_env):
    with Db(Db.READ_ONLY) as db:
        t = next(iter(db.threads("*")))
        ts = ThreadSummary.from_notmuch(t)
        del t

        assert ts.thread_id
        assert ts.total_messages >= 1
        assert isinstance(ts.tags, list)
        assert len(ts.authors) >= 1
        name, _unread = ts.authors[0]
        assert isinstance(name, str) and name
        # new-mail tags applied by notmuch new ([new] tags=unread;inbox;)
        assert "inbox" in ts.tags
        assert ts.unread == ("unread" in ts.tags)


def test_message_summary(db_env):
    with Db(Db.READ_ONLY) as db:
        m = next(iter(db.messages("*")))
        ms = MessageSummary.from_notmuch(m)
        del m
        assert ms.mid and ms.thread_id
        assert ms.filename.endswith(".eml")
        assert ms.time >= 0


def test_tag_thread_and_refresh(db_env):
    with Db(Db.READ_ONLY) as db:
        ts = ThreadSummary.from_notmuch(next(iter(db.threads("*"))))

    with Db(Db.READ_WRITE) as db:
        assert thread_add_tag(db, ts.thread_id, "py-test-tag")

    with Db(Db.READ_ONLY) as db:
        assert ts.refresh(db)
        assert "py-test-tag" in ts.tags

    with Db(Db.READ_WRITE) as db:
        assert thread_remove_tag(db, ts.thread_id, "py-test-tag")

    with Db(Db.READ_ONLY) as db:
        assert ts.refresh(db)
        assert "py-test-tag" not in ts.tags


def test_tag_sanitize_and_check(db_env):
    assert Db.sanitize_tag("  x  ") == "x"
    assert Db.check_tag("ok")
    assert not Db.check_tag("")
    assert not Db.check_tag('has"quote')
    assert not Db.check_tag("x" * 300)


def test_load_tags(db_env):
    with Db(Db.READ_ONLY) as db:
        db.load_tags()
    assert "inbox" in Db.tags


def test_on_thread_and_on_message(db_env):
    with Db(Db.READ_ONLY) as db:
        ms = MessageSummary.from_notmuch(next(iter(db.messages("*"))))

        got = db.on_message(ms.mid, lambda m: m.messageid if m else None)
        assert got == ms.mid

        got = db.on_thread(ms.thread_id, lambda t: t.threadid if t else None)
        assert got == ms.thread_id

        assert db.on_message("no-such-mid@nowhere", lambda m: m) is None


def test_rw_excludes_ro(db_env):
    """RW open must wait for RO to close (port of the db.cc gate)."""
    events = []

    ro = Db(Db.READ_ONLY)

    def writer():
        events.append("rw-wait")
        with Db(Db.READ_WRITE):
            events.append("rw-open")

    t = threading.Thread(target=writer)
    t.start()
    time.sleep(0.3)
    assert events == ["rw-wait"]  # writer blocked while RO open

    ro.close()
    t.join(timeout=5)
    assert events == ["rw-wait", "rw-open"]


def test_revision_and_lastmod(db_env):
    with Db(Db.READ_ONLY) as db:
        rev0 = db.get_revision()
        ts = ThreadSummary.from_notmuch(next(iter(db.threads("*"))))

    with Db(Db.READ_WRITE) as db:
        thread_add_tag(db, ts.thread_id, "lastmod-test")
        rev1 = db.get_revision()

    assert rev1 > rev0

    with Db(Db.READ_ONLY) as db:
        changed = [t.threadid for t in db.threads(f"lastmod:{rev0}..{rev1}")]
        assert ts.thread_id in changed

    with Db(Db.READ_WRITE) as db:
        thread_remove_tag(db, ts.thread_id, "lastmod-test")
