from astroid_mail.ptree_json import PTree, PTreeBadPath, read_ini

import pytest


def test_put_get_paths():
    t = PTree()
    t.put("a.b.c", "x")
    assert t.get("a.b.c") == "x"
    assert "a.b.c" in t
    assert "a.b.d" not in t
    with pytest.raises(PTreeBadPath):
        t.get("a.b.d")
    assert t.get("a.b.d", "fallback") == "fallback"


def test_boost_string_coercions():
    t = PTree()
    t.put("v.b1", "true")
    t.put("v.b2", "false")
    t.put("v.i", "42")
    t.put("v.f", "0.5")
    assert t.get_bool("v.b1") is True
    assert t.get_bool("v.b2") is False
    assert t.get_int("v.i") == 42
    assert t.get_float("v.f") == 0.5


def test_native_types_roundtrip_as_strings():
    t = PTree()
    t.put("x.b", True)
    t.put("x.i", 7)
    t.put("x.f", 0.5)
    s = t.dumps()
    u = PTree.loads(s)
    # boost writes all leaves as strings; ours must too
    assert u.get("x.b") == "true"
    assert u.get("x.i") == "7"
    assert u.get("x.f") == "0.5"
    assert u.get_bool("x.b") is True
    assert u.get_int("x.i") == 7
    assert u.get_float("x.f") == 0.5


def test_merge_overlays_leaves():
    base = PTree()
    base.put("a.x", "1")
    base.put("a.y", "2")
    over = PTree()
    over.put("a.y", "3")
    over.put("b.z", "4")
    base.merge(over)
    assert base.get("a.x") == "1"
    assert base.get("a.y") == "3"
    assert base.get("b.z") == "4"


def test_get_child():
    t = PTree()
    t.put("accounts.work.email", "a@b.c")
    t.put("accounts.home.email", "d@e.f")
    accounts = t.get_child("accounts")
    assert len(accounts) == 2
    assert set(dict(accounts.items()).keys()) == {"work", "home"}


def test_read_ini(tmp_path):
    p = tmp_path / "ini"
    p.write_text("""
# comment
[database]
path=/mail/db

[search]
exclude_tags=deleted;spam;
""")
    t = read_ini(p)
    assert t.get("database.path") == "/mail/db"
    assert t.get("search.exclude_tags") == "deleted;spam;"
