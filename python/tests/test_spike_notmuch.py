"""Spike (a): notmuch2 bindings cover everything db.py needs.

API notes captured here (verified against python3-notmuch2 0.38):
* Thread: threadid, subject, authors (raw string), first/last (unix time),
  matched, len(thread) = total messages, iterate for messages, .tags
* Message: messageid, threadid, date, path, filenames(), tags, frozen(),
  tags.to_maildir_flags()/from_maildir_flags()
* Database: threads()/messages()/count_messages()/count_threads(),
  revision().rev, exclude_tags= kwarg on queries.
* IMPORTANT: child objects must not outlive the Database (destructors
  touch the db) — del them before close, never return them.
"""

import pytest

notmuch2 = pytest.importorskip("notmuch2")


def test_query_threads_and_fields(notmuch_db):
    maildir, _ = notmuch_db
    db = notmuch2.Database(str(maildir), mode=notmuch2.Database.MODE.READ_ONLY)
    try:
        threads = db.threads("*")
        t = next(iter(threads))
        # the fields NotmuchThread copies (db.cc)
        assert isinstance(t.threadid, str)
        assert isinstance(t.subject, str)
        assert isinstance(t.authors, str)  # raw authors string
        assert len(t) >= 1                 # total messages
        assert t.matched >= 1
        tags = list(t.tags)
        assert all(isinstance(x, str) for x in tags)
        _ = t.first, t.last  # oldest/newest unix timestamps
        del t, threads
    finally:
        db.close()


def test_exclude_tags_and_counts(notmuch_db):
    maildir, _ = notmuch_db
    db = notmuch2.Database(str(maildir), mode=notmuch2.Database.MODE.READ_ONLY)
    try:
        all_count = db.count_messages("*")
        assert all_count > 0
        n = db.count_messages("*", exclude_tags=["spam", "deleted"])
        assert n <= all_count
        assert db.count_threads("tag:inbox") >= 0
    finally:
        db.close()


def test_revision_increases_on_change(notmuch_db):
    maildir, _ = notmuch_db
    db = notmuch2.Database(str(maildir), mode=notmuch2.Database.MODE.READ_WRITE)
    try:
        rev0 = db.revision().rev
        msg = next(iter(db.messages("*")))
        mid = msg.messageid
        with msg.frozen():
            msg.tags.add("astroid-spike")
        rev1 = db.revision().rev
        assert rev1 > rev0

        # lastmod query finds the changed message (poll partial refresh)
        found = [m.messageid for m in db.messages(f"lastmod:{rev0}..{rev1}")]
        assert mid in found

        with msg.frozen():
            msg.tags.discard("astroid-spike")
        del msg
    finally:
        db.close()


def test_maildir_flags_sync(notmuch_db):
    maildir, _ = notmuch_db
    db = notmuch2.Database(str(maildir), mode=notmuch2.Database.MODE.READ_WRITE)
    try:
        msg = next(iter(db.messages("*")))
        # maildir flag sync used when maildir.synchronize_flags (db.cc)
        assert hasattr(msg.tags, "to_maildir_flags")
        assert hasattr(msg.tags, "from_maildir_flags")
        del msg
    finally:
        db.close()
