"""AddSentMessage / AddDraftMessage / RemoveMessage against a real
notmuch test DB."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

notmuch2 = pytest.importorskip("notmuch2")

from astroid_mail.actions import (
    ActionManager, AddDraftMessage, AddSentMessage, RemoveMessage,
)
from astroid_mail.config import Config
from astroid_mail.db import Db


@pytest.fixture()
def db_env(notmuch_db, config_env, monkeypatch):
    maildir, nm_config = notmuch_db
    monkeypatch.setenv("NOTMUCH_CONFIG", str(nm_config))
    cfg = Config()
    Db.init(cfg)
    yield maildir, nm_config
    Db.path_db = None
    Db.excluded_tags = []


@pytest.fixture()
def manager(db_env):
    pending: list = []
    am = ActionManager(dispatch=lambda fn: pending.append(fn))
    yield am, pending
    am.close()


def wait_drained(am, pending, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        with am._cv:
            empty = not am._actions
        if empty and pending:
            break
        time.sleep(0.02)
    time.sleep(0.05)
    while pending:
        pending.pop(0)()


def _write_eml(maildir: Path, mid: str, subject: str, body: str = "x") -> Path:
    f = maildir / f"{mid.replace('@', '_')}.eml"
    f.write_text(textwrap.dedent(f"""\
        From: Charlie Root <root@localhost>
        To: bob@example.org
        Subject: {subject}
        Message-Id: <{mid}>
        Date: Thu, 01 Jan 2026 10:00:00 +0000

        {body}
        """))
    return f


def test_add_sent_message_indexes_with_sent_tags(db_env, manager):
    maildir, _ = db_env
    am, pending = manager

    sent_dir = maildir / "sent"
    sent_dir.mkdir()
    f = _write_eml(sent_dir, "sent-1@example.org", "sent test")

    am.doit(AddSentMessage(f, extra_tags=["extra"]))
    wait_drained(am, pending)

    with Db(Db.READ_ONLY) as db:
        msg = next(iter(db.messages("id:sent-1@example.org")))
        tags = sorted(str(t) for t in msg.tags)
        del msg
    assert "sent" in tags
    assert "extra" in tags
    assert "unread" not in tags


def test_add_sent_adds_replied_to_in_reply_to(db_env, manager):
    maildir, _ = db_env
    am, pending = manager
    # find a message already in the db to use as in_reply_to target
    with Db(Db.READ_ONLY) as db:
        target = next(iter(db.messages("*"))).messageid

    sent_dir = maildir / "sent"; sent_dir.mkdir()
    f = _write_eml(sent_dir, "reply-1@example.org", "Re: x")

    am.doit(AddSentMessage(f, in_reply_to=target))
    wait_drained(am, pending)

    with Db(Db.READ_ONLY) as db:
        orig = next(iter(db.messages(f"id:{target}")))
        tags = sorted(str(t) for t in orig.tags)
        del orig
    assert "replied" in tags


def test_add_draft_message_applies_draft_tags(db_env, manager):
    maildir, _ = db_env
    am, pending = manager
    drafts = maildir / "drafts"; drafts.mkdir()
    f = _write_eml(drafts, "draft-1@example.org", "draft work")

    am.doit(AddDraftMessage(f))
    wait_drained(am, pending)

    with Db(Db.READ_ONLY) as db:
        msg = next(iter(db.messages("id:draft-1@example.org")))
        tags = sorted(str(t) for t in msg.tags)
        del msg
    # Db.draft_tags defaults to ["draft"]
    assert "draft" in tags


def test_remove_message_clears_file_and_index(db_env, manager):
    maildir, _ = db_env
    am, pending = manager
    drafts = maildir / "drafts2"; drafts.mkdir()
    f = _write_eml(drafts, "rm-1@example.org", "to delete")

    # first add it
    am.doit(AddDraftMessage(f))
    wait_drained(am, pending)
    assert f.is_file()

    am.doit(RemoveMessage(f))
    wait_drained(am, pending)
    assert not f.exists()

    with Db(Db.READ_ONLY) as db:
        assert db.count_messages("id:rm-1@example.org") == 0
