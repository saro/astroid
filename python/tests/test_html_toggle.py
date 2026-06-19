"""Switch-to-HTML (page_client.toggle_html) and remote-image gating.

Exercises the serialization logic directly: which alternative part is
marked ``use`` before/after toggling prefer_html, without needing a
display.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("GMime", "3.0")

from astroid_mail.config import Config  # noqa: E402
from astroid_mail.models.message_thread import Message  # noqa: E402
from astroid_mail.modes.thread_view.page_client import PageClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MAIL = REPO_ROOT / "tests" / "mail" / "test_mail"


def _cfg():
    c = Config(no_load=True)
    c.config = c.setup_default_config(True)
    return c


class _FakeTV:
    """Just enough of ThreadView for PageClient.make_message."""
    def __init__(self, cfg, messages):
        self.config = cfg
        self.edit_mode = False
        self.home_uri = "file:///x/"
        self.state = {}
        self.mthread = SimpleNamespace(messages=messages)


def _used_mime_types(msg_dict):
    """Collect mime_type of every viewable chunk that is marked use."""
    used = []

    def walk(c):
        if c is None:
            return
        if c.get("viewable") and c.get("use"):
            used.append(c["mime_type"])
        for k in c.get("kids", []):
            walk(k)
    walk(msg_dict.get("root"))
    return used


def test_default_shows_plain():
    cfg = _cfg()
    m = Message(filename=str(MAIL / "multipart.eml"))
    tv = _FakeTV(cfg, [m])
    pc = PageClient(tv)

    msg = pc.make_message(m)
    used = _used_mime_types(msg)
    assert "text/plain" in used
    assert "text/html" not in used


def test_has_html_alternative_detected():
    m = Message(filename=str(MAIL / "multipart.eml"))
    assert PageClient._has_html_alternative(m) is True

    plain_only = Message(filename=str(MAIL / "msg1.eml"))
    assert PageClient._has_html_alternative(plain_only) is False


def test_toggle_html_switches_used_part():
    cfg = _cfg()
    m = Message(filename=str(MAIL / "multipart.eml"))
    tv = _FakeTV(cfg, [m])

    sent = []
    pc = PageClient(tv)
    pc.send = lambda payload, on_ack=None: sent.append(payload)

    # initial render to populate state
    pc.make_message(m)

    # toggle -> prefer html
    assert pc.toggle_html(m) is True
    assert tv.state[m]["prefer_html"] is True
    # the update_message payload now uses the html part
    update = [s for s in sent if s["type"] == "update_message"][-1]
    used = _used_mime_types(update["message"])
    assert "text/html" in used
    assert "text/plain" not in used

    # toggle back
    assert pc.toggle_html(m) is True
    assert tv.state[m]["prefer_html"] is False
    update = [s for s in sent if s["type"] == "update_message"][-1]
    used = _used_mime_types(update["message"])
    assert "text/plain" in used
    assert "text/html" not in used


def test_toggle_html_noop_without_alternative():
    cfg = _cfg()
    m = Message(filename=str(MAIL / "msg1.eml"))
    tv = _FakeTV(cfg, [m])
    pc = PageClient(tv)
    pc.send = lambda *a, **k: None
    assert pc.toggle_html(m) is False


def test_set_remote_images_sends_allow_then_updates():
    cfg = _cfg()
    m = Message(filename=str(MAIL / "multipart.eml"))
    tv = _FakeTV(cfg, [m])
    pc = PageClient(tv)
    sent = []
    pc.send = lambda payload, on_ack=None: sent.append(payload)

    pc.set_remote_images(True)
    # the allow message comes before the re-render
    assert sent[0] == {"type": "allow_remote_images", "allow": True}
    assert any(s["type"] == "update_message" for s in sent[1:])
