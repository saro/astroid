"""Message/Chunk model tests against the C++ test corpus."""

from pathlib import Path

import pytest

notmuch2 = pytest.importorskip("notmuch2")

from astroid_mail.config import Config  # noqa: E402
from astroid_mail.db import Db, ThreadSummary  # noqa: E402
from astroid_mail.models.chunk import text_to_html  # noqa: E402
from astroid_mail.models.message_thread import Message, MessageThread  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MAIL = REPO_ROOT / "tests" / "mail" / "test_mail"


@pytest.fixture()
def db_env(notmuch_db, config_env, monkeypatch):
    maildir, nm_config = notmuch_db
    monkeypatch.setenv("NOTMUCH_CONFIG", str(nm_config))
    cfg = Config()
    Db.init(cfg)
    yield maildir
    Db.path_db = None
    Db.excluded_tags = []


def test_load_simple_message():
    m = Message(filename=str(MAIL / "msg1.eml"))
    assert m.subject
    assert m.mid
    assert "@" in m.sender or m.sender
    assert not m.missing_content
    assert m.root is not None
    assert len(m.viewable_parts()) >= 1
    text = m.plain_text()
    assert "experiment" in text


def test_missing_file():
    m = Message(filename="/nonexistent/nope.eml", mid="x@y")
    assert m.missing_content
    assert m.mid == "x@y"


def test_safe_mid():
    m = Message(filename=str(MAIL / "msg1.eml"))
    assert "/" not in m.safe_mid()
    assert " " not in m.safe_mid()


def test_text_to_html_quoting():
    html = text_to_html("hello\n> quoted\n>> deep\nplain")
    assert '<blockquote class="level_1">' in html
    assert '<blockquote class="level_2">' in html
    assert html.count("</blockquote>") == 2
    assert "hello<br>" in html


def test_text_to_html_links_and_escape():
    html = text_to_html("see https://example.com/x?a=1 <b> & such")
    assert '<a href="https://example.com/x?a=1">' in html
    assert "&lt;b&gt;" in html
    assert "&amp; such" in html


def test_message_thread_loads_in_order(db_env):
    with Db(Db.READ_ONLY) as db:
        ts = ThreadSummary.from_notmuch(next(iter(db.threads("*"))))
        mt = MessageThread(ts)
        mt.load_messages(db)

    assert len(mt.messages) == ts.total_messages
    for m in mt.messages:
        assert m.mid
        assert m.in_notmuch
        assert m.level >= 0


def test_attachment_parts():
    # mime-message-no-content-type.eml has structure worth walking
    m = Message(filename=str(MAIL / "mime-message-no-content-type.eml"))
    assert m.root is not None
    parts = m.all_parts()
    assert len(parts) >= 1


def test_broken_charset_fallback():
    # convert_error.eml declares a charset its body bytes violate
    m = Message(filename=str(MAIL / "convert_error.eml"))
    if m.missing_content:
        pytest.skip("fixture missing")
    # must not raise
    for c in m.viewable_parts():
        text = c.viewable_text(html=False)
        assert isinstance(text, str)
