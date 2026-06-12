"""Port of src/db.cc on top of the notmuch2 bindings.

Locking model (same as C++):
* any number of read-only Db instances may be open simultaneously,
* a read-write Db waits for all read-onlys to close and blocks new ones
  while it is open,
* opens are retried while notmuch returns Xapian errors (e.g. during an
  external `notmuch new`).

notmuch2 objects are NOT thread-safe and child objects must not outlive
the database — every consumer copies scalar data out (ThreadSummary,
MessageSummary) and never hands notmuch2 objects across threads.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import notmuch2

from .log import log
from .ptree_json import PTreeBadPath
from .utils.address import Address
from .utils.misc import expand

NOTMUCH_TAG_MAX = 200


class DatabaseError(Exception):
    pass


class Db:
    READ_ONLY = "ro"
    READ_WRITE = "rw"

    # class-level state (port of the statics in db.hh)
    path_db: Path | None = None
    excluded_tags: list[str] = []
    sent_tags: list[str] = ["sent"]
    draft_tags: list[str] = ["draft"]
    maildir_synchronize_flags: bool = False
    tags: list[str] = []

    db_open_timeout = 30      # seconds (db.hh: db_open_timeout)
    db_open_delay = 1         # seconds between retries

    # RO/RW gate (port of db.cc:198-230)
    _gate = threading.Condition()
    _ro_open = 0
    _rw_open = False

    @classmethod
    def init(cls, config) -> None:
        """Port of Db::init — read settings from the notmuch config."""
        if not config.has_notmuch_config:
            raise DatabaseError("db: error: no notmuch config file found.")

        nm = config.notmuch_config

        try:
            db_path = nm.get_str("database.path")
        except PTreeBadPath:
            raise DatabaseError("db: error: no database path specified")

        log.info("db path: %s", db_path)
        cls.path_db = expand(db_path).absolute()

        try:
            excluded = nm.get_str("search.exclude_tags")
        except PTreeBadPath:
            raise DatabaseError(
                "db: error: no search.exclude_tags defined in notmuch-config")
        cls.excluded_tags = sorted(t.strip() for t in excluded.split(";")
                                   if t.strip())

        sent = config.config.get_str("mail.sent_tags")
        cls.sent_tags = sorted(t.strip() for t in sent.split(",") if t.strip())

        try:
            cls.maildir_synchronize_flags = nm.get_bool("maildir.synchronize_flags")
        except PTreeBadPath:
            raise DatabaseError(
                "db: error: no maildir.maildir_synchronize_flags defined "
                "in notmuch-config")
        except ValueError:
            bad = nm.get_str("maildir.synchronize_flags")
            if bad:
                log.error("db: error: bad argument '%s' for "
                          "maildir.maildir_synchronize_flags in notmuch-config "
                          "(expected yes/no)", bad)
            cls.maildir_synchronize_flags = False

    # -- gate -----------------------------------------------------------------

    @classmethod
    def _acquire_ro(cls):
        with cls._gate:
            cls._gate.wait_for(lambda: not cls._rw_open)
            cls._ro_open += 1

    @classmethod
    def _release_ro(cls):
        with cls._gate:
            cls._ro_open -= 1
            cls._gate.notify_all()

    @classmethod
    def _acquire_rw(cls):
        with cls._gate:
            cls._gate.wait_for(lambda: not cls._rw_open and cls._ro_open == 0)
            cls._rw_open = True

    @classmethod
    def _release_rw(cls):
        with cls._gate:
            cls._rw_open = False
            cls._gate.notify_all()

    # -- instance ---------------------------------------------------------------

    def __init__(self, mode: str = READ_ONLY):
        if Db.path_db is None:
            raise DatabaseError("db: not initialized (call Db.init first)")
        if mode not in (Db.READ_ONLY, Db.READ_WRITE):
            raise ValueError("db: mode must be read-only or read-write")

        self.mode = mode
        self.nm_db: notmuch2.Database | None = None
        self._closed = False

        if mode == Db.READ_WRITE:
            Db._acquire_rw()
            nm_mode = notmuch2.Database.MODE.READ_WRITE
        else:
            Db._acquire_ro()
            nm_mode = notmuch2.Database.MODE.READ_ONLY

        waited = 0
        try:
            while True:
                try:
                    self.nm_db = notmuch2.Database(str(Db.path_db), mode=nm_mode)
                    break
                except notmuch2.XapianError:
                    if waited > Db.db_open_timeout:
                        raise
                    log.error("db: error: could not open db %s, waited %s of "
                              "maximum %s seconds.", mode, waited,
                              Db.db_open_timeout)
                    time.sleep(Db.db_open_delay)
                    waited += Db.db_open_delay
        except Exception:
            self._release_gate()
            raise

    def _release_gate(self):
        if self.mode == Db.READ_WRITE:
            Db._release_rw()
        else:
            Db._release_ro()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self.nm_db is not None:
            try:
                self.nm_db.close()
            finally:
                self.nm_db = None
        self._release_gate()

    def __enter__(self) -> "Db":
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __del__(self):
        if not self._closed and self.nm_db is not None:
            self.close()

    # -- queries -------------------------------------------------------------

    def get_revision(self) -> int:
        return self.nm_db.revision().rev

    def threads(self, query: str, sort=None, exclude: bool = True):
        kwargs = {}
        if exclude and Db.excluded_tags:
            kwargs["exclude_tags"] = Db.excluded_tags
        if sort is not None:
            kwargs["sort"] = sort
        return self.nm_db.threads(query, **kwargs)

    def messages(self, query: str, exclude: bool = True):
        kwargs = {}
        if exclude and Db.excluded_tags:
            kwargs["exclude_tags"] = Db.excluded_tags
        return self.nm_db.messages(query, **kwargs)

    def count_messages(self, query: str, exclude: bool = True) -> int:
        kwargs = {}
        if exclude and Db.excluded_tags:
            kwargs["exclude_tags"] = Db.excluded_tags
        return self.nm_db.count_messages(query, **kwargs)

    def count_threads(self, query: str, exclude: bool = True) -> int:
        kwargs = {}
        if exclude and Db.excluded_tags:
            kwargs["exclude_tags"] = Db.excluded_tags
        return self.nm_db.count_threads(query, **kwargs)

    def on_thread(self, thread_id: str, func):
        """Run func with the notmuch2 thread object (or None). The object
        must not escape func."""
        threads = self.nm_db.threads(f"thread:{thread_id}")
        try:
            t = next(iter(threads))
        except StopIteration:
            log.error("db: could not find thread: %s", thread_id)
            return func(None)
        try:
            return func(t)
        finally:
            del t

    def on_message(self, mid: str, func):
        """Run func with the notmuch2 message object (or None)."""
        try:
            m = self.nm_db.find(mid)
        except LookupError:
            log.error("db: could not find message: %s", mid)
            return func(None)
        try:
            return func(m)
        finally:
            del m

    def load_tags(self):
        Db.tags = sorted(str(t) for t in self.nm_db.tags)
        log.debug("db: loaded %s tags.", len(Db.tags))

    # -- tags ---------------------------------------------------------------

    @staticmethod
    def sanitize_tag(tag: str) -> str:
        return tag.strip()

    @staticmethod
    def check_tag(tag: str) -> bool:
        if not tag:
            log.error("nmt: invalid tag, empty.")
            return False
        if '"' in tag:
            log.error("nmt: invalid char in tag: \"")
            return False
        if len(tag) > NOTMUCH_TAG_MAX:
            log.error("nmt: error: maximum tag length is: %s", NOTMUCH_TAG_MAX)
            return False
        return True


# -- scalar copies ------------------------------------------------------------

@dataclass
class ThreadSummary:
    """Port of NotmuchThread: scalar copy of one notmuch thread."""
    thread_id: str = ""
    subject: str = ""
    newest_date: int = 0
    oldest_date: int = 0
    total_messages: int = 0
    unread: bool = False
    attachment: bool = False
    flagged: bool = False
    tags: list[str] = field(default_factory=list)
    # (author fail-safe name, has unread message)
    authors: list[tuple[str, bool]] = field(default_factory=list)
    in_notmuch: bool = True

    @classmethod
    def from_notmuch(cls, t) -> "ThreadSummary":
        ts = cls(thread_id=t.threadid, subject=t.subject or "")
        ts.newest_date = int(t.last)
        ts.oldest_date = int(t.first)
        ts.total_messages = len(t)

        ts.tags = sorted(str(x) for x in t.tags)
        ts.unread = "unread" in ts.tags
        ts.attachment = "attachment" in ts.tags
        ts.flagged = "flagged" in ts.tags

        # port of NotmuchThread::get_authors: iterate messages, From header,
        # mark authors that have unread messages
        authors: list[tuple[str, bool]] = []
        for m in t:
            try:
                frm = m.header("From")
            except LookupError:
                frm = ""
            name = Address(frm).fail_safe_name() if frm else ""
            m_unread = "unread" in m.tags
            for i, (a, u) in enumerate(authors):
                if a == name:
                    if m_unread and not u:
                        authors[i] = (a, True)
                    break
            else:
                authors.append((name, m_unread))
            del m
        ts.authors = authors
        return ts

    def refresh(self, db: Db) -> bool:
        def doit(t):
            if t is None:
                self.in_notmuch = False
                return False
            fresh = ThreadSummary.from_notmuch(t)
            self.__dict__.update(fresh.__dict__)
            self.in_notmuch = True
            return True
        return db.on_thread(self.thread_id, doit)


@dataclass
class MessageSummary:
    """Port of NotmuchMessage (scalar copy)."""
    mid: str = ""
    thread_id: str = ""
    subject: str = ""
    sender: str = ""
    time: int = 0
    filename: str = ""
    tags: list[str] = field(default_factory=list)
    unread: bool = False
    attachment: bool = False
    flagged: bool = False

    @classmethod
    def from_notmuch(cls, m) -> "MessageSummary":
        ms = cls(mid=m.messageid, thread_id=m.threadid or "")
        try:
            ms.subject = m.header("Subject")
        except LookupError:
            pass
        try:
            ms.sender = m.header("From")
        except LookupError:
            pass
        ms.time = int(m.date)
        ms.filename = str(m.path)
        ms.tags = sorted(str(t) for t in m.tags)
        ms.unread = "unread" in ms.tags
        ms.attachment = "attachment" in ms.tags
        ms.flagged = "flagged" in ms.tags
        return ms


# -- tag operations (port of NotmuchThread/NotmuchMessage add_tag/remove_tag) --

def _sync_maildir_flags(nm_msg):
    if Db.maildir_synchronize_flags:
        nm_msg.tags.to_maildir_flags()


def thread_add_tag(db: Db, thread_id: str, tag: str) -> bool:
    tag = Db.sanitize_tag(tag)
    if not Db.check_tag(tag):
        return False

    changed = False
    for m in db.messages(f"thread:{thread_id}", exclude=False):
        with m.frozen():
            if tag not in m.tags:
                m.tags.add(tag)
                changed = True
        _sync_maildir_flags(m)
        del m
    return changed


def thread_remove_tag(db: Db, thread_id: str, tag: str) -> bool:
    tag = Db.sanitize_tag(tag)
    if not Db.check_tag(tag):
        return False

    changed = False
    for m in db.messages(f"thread:{thread_id}", exclude=False):
        with m.frozen():
            if tag in m.tags:
                m.tags.discard(tag)
                changed = True
        _sync_maildir_flags(m)
        del m
    return changed


def message_add_tag(db: Db, mid: str, tag: str) -> bool:
    tag = Db.sanitize_tag(tag)
    if not Db.check_tag(tag):
        return False

    def doit(m):
        if m is None:
            return False
        with m.frozen():
            m.tags.add(tag)
        _sync_maildir_flags(m)
        return True
    return db.on_message(mid, doit)


def message_remove_tag(db: Db, mid: str, tag: str) -> bool:
    tag = Db.sanitize_tag(tag)
    if not Db.check_tag(tag):
        return False

    def doit(m):
        if m is None:
            return False
        with m.frozen():
            m.tags.discard(tag)
        _sync_maildir_flags(m)
        return True
    return db.on_message(mid, doit)
