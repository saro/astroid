"""Saved searches: cross-compatibility with the C++ JSON format."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from astroid_mail.config import Config
from astroid_mail.saved_searches import (
    SavedSearchesStore, load_searches, write_searches,
)

# A real ~/.config/astroid/searches written by the C++ implementation.
# Note the duplicate "none" keys in both saved (when user did 's' twice with
# no name) and history. This is boost::property_tree's JSON dump style.
CPP_FORMAT = """\
{
    "saved": {
        "inbox": "tag:inbox",
        "none": "tag:foo",
        "none": "tag:bar"
    },
    "history": {
        "none": "from:alice",
        "none": "tag:flagged",
        "none": "subject:fwd"
    }
}
"""


def cfg():
    c = Config(no_load=True)
    c.config = c.setup_default_config(True)
    return c


def test_load_preserves_duplicate_keys(tmp_path):
    p = tmp_path / "searches"
    p.write_text(CPP_FORMAT)

    saved, history = load_searches(p)
    assert saved == [
        ("inbox", "tag:inbox"),
        ("none", "tag:foo"),
        ("none", "tag:bar"),
    ]
    assert history == ["from:alice", "tag:flagged", "subject:fwd"]


def test_roundtrip_writes_cpp_compatible(tmp_path):
    p = tmp_path / "searches"
    saved = [
        ("inbox", "tag:inbox"),
        ("none", "tag:foo"),
        ("none", "tag:bar"),
    ]
    history = ["from:alice", "tag:flagged"]
    write_searches(p, saved, history)

    text = p.read_text()
    # duplicate "none" must literally appear (twice in saved, twice in history)
    assert text.count('"saved":') == 1
    assert text.count('"none": "tag:foo"') == 1
    assert text.count('"none": "tag:bar"') == 1
    assert text.count('"none": "from:alice"') == 1
    assert text.count('"none": "tag:flagged"') == 1
    # readable back into the same structure
    saved2, history2 = load_searches(p)
    assert saved2 == saved
    assert history2 == history


def test_missing_file_is_empty(tmp_path):
    saved, history = load_searches(tmp_path / "absent")
    assert saved == [] and history == []


def test_invalid_json_is_empty(tmp_path):
    p = tmp_path / "searches"
    p.write_text("not json")
    saved, history = load_searches(p)
    assert saved == [] and history == []


def test_store_add_remove_and_history(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    c = cfg()
    c.std_paths.searches_file = tmp_path / "searches"

    store = SavedSearchesStore(c)
    store.load()
    assert store.saved == [] and store.history == []

    store.add_saved("tag:work")  # no name -> "none"
    store.add_saved("tag:unread", name="unread")
    store.push_history("from:bob")
    store.push_history("tag:flagged")
    store.push_history("from:bob")  # moves to front, dedupe

    assert store.history == ["from:bob", "tag:flagged"]

    store.save()

    # round-trip via the real file
    store2 = SavedSearchesStore(c)
    store2.load()
    assert store2.saved == [("none", "tag:work"), ("unread", "tag:unread")]
    assert store2.history == ["from:bob", "tag:flagged"]


def test_history_cap_on_save(tmp_path):
    c = cfg()
    c.config.put("saved_searches.history_lines", 2)
    c.std_paths.searches_file = tmp_path / "searches"

    store = SavedSearchesStore(c)
    for q in ("q1", "q2", "q3", "q4"):
        store.push_history(q)
    # most-recent-first: q4, q3, q2, q1
    store.save()
    store2 = SavedSearchesStore(c)
    store2.load()
    assert store2.history == ["q4", "q3"]


def test_save_history_disabled_drops_history_on_disk(tmp_path):
    c = cfg()
    c.config.put("saved_searches.save_history", False)
    c.std_paths.searches_file = tmp_path / "searches"
    store = SavedSearchesStore(c)
    store.add_saved("tag:x", "x")
    store.push_history("hot")
    store.save()
    # on disk: no history entries
    store2 = SavedSearchesStore(c)
    store2.load()
    assert store2.saved == [("x", "tag:x")]
    assert store2.history == []


def test_remove_saved(tmp_path):
    c = cfg()
    c.std_paths.searches_file = tmp_path / "searches"
    store = SavedSearchesStore(c)
    store.add_saved("q1", "a")
    store.add_saved("q2", "b")
    assert store.remove_saved("q1")
    assert not store.remove_saved("nope")
    assert store.saved == [("b", "q2")]
