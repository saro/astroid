"""Thread index sort order honours thread_index.sort_order (default: newest)."""

from __future__ import annotations

import textwrap
import time

import pytest

notmuch2 = pytest.importorskip("notmuch2")

from gi.repository import Gio  # noqa: E402

from astroid_mail.config import Config  # noqa: E402
from astroid_mail.db import Db  # noqa: E402
from astroid_mail.modes.thread_index.query_loader import (  # noqa: E402
    QueryLoader, ThreadItem, sort_from_name,
)


@pytest.fixture()
def db_env(notmuch_db, config_env, monkeypatch):
    maildir, nm_config = notmuch_db
    monkeypatch.setenv("NOTMUCH_CONFIG", str(nm_config))
    cfg = Config()
    Db.init(cfg)
    yield cfg, maildir, nm_config
    Db.path_db = None
    Db.excluded_tags = []


def drain(loader, pending, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not loader.loading and not pending:
            break
        time.sleep(0.05)
        while pending:
            pending.pop(0)()
    while pending:
        pending.pop(0)()


def test_sort_from_name_default_newest():
    assert sort_from_name("") == notmuch2.Database.SORT.NEWEST_FIRST
    assert sort_from_name("Newest") == notmuch2.Database.SORT.NEWEST_FIRST
    assert sort_from_name("oldest") == notmuch2.Database.SORT.OLDEST_FIRST
    assert sort_from_name("unsorted") == notmuch2.Database.SORT.UNSORTED
    assert sort_from_name("garbage") == notmuch2.Database.SORT.NEWEST_FIRST


def test_default_sort_is_newest_first(db_env):
    pending: list = []
    store = Gio.ListStore(item_type=ThreadItem)
    loader = QueryLoader(store, dispatch=lambda fn: pending.append(fn))
    loader.start("*")
    drain(loader, pending)

    dates = [store.get_item(i).summary.newest_date
             for i in range(store.get_n_items())]
    assert len(dates) >= 2
    # newest first: monotonically non-increasing
    assert all(dates[i] >= dates[i + 1] for i in range(len(dates) - 1))


def test_oldest_first_when_configured(db_env):
    pending: list = []
    store = Gio.ListStore(item_type=ThreadItem)
    loader = QueryLoader(store, dispatch=lambda fn: pending.append(fn),
                         sort=notmuch2.Database.SORT.OLDEST_FIRST)
    loader.start("*")
    drain(loader, pending)

    # notmuch OLDEST_FIRST sorts threads by the oldest matching message
    dates = [store.get_item(i).summary.oldest_date
             for i in range(store.get_n_items())]
    assert len(dates) >= 2
    assert all(dates[i] <= dates[i + 1] for i in range(len(dates) - 1))
