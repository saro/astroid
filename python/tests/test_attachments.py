"""Attachments come first in j/k order; Enter saves to tmp and opens
with attachment.external_open_cmd."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("GMime", "3.0")

from astroid_mail.config import Config  # noqa: E402
from astroid_mail.models.message_thread import Message  # noqa: E402
from astroid_mail.modes.thread_view.page_client import PageClient  # noqa: E402
from astroid_mail.modes.thread_view.thread_view import ThreadView  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MAIL = REPO_ROOT / "tests" / "mail" / "test_mail"


def _cfg():
    c = Config(no_load=True)
    c.config = c.setup_default_config(True)
    return c


class _FakeTV:
    def __init__(self, cfg, messages):
        self.config = cfg
        self.edit_mode = False
        self.home_uri = "file:///x/"
        self.state = {}
        self.mthread = SimpleNamespace(messages=messages)


def test_attachment_elements_come_before_parts():
    cfg = _cfg()
    m = Message(filename=str(MAIL / "msg1.eml"))
    assert m.attachments(), "fixture must have an attachment"

    tv = _FakeTV(cfg, [m])
    pc = PageClient(tv)
    pc.make_message(m)

    types = [e.type for e in tv.state[m]["elements"]]
    assert types[0] == "empty"
    # every attachment element precedes every part element
    att_idx = [i for i, t in enumerate(types) if t == "attachment"]
    part_idx = [i for i, t in enumerate(types) if t == "part"]
    assert att_idx, f"no attachment elements: {types}"
    if part_idx:
        assert max(att_idx) < min(part_idx), types


class _ActStub:
    """Stub with just what _key_activate/_open_attachment need."""
    _key_activate = ThreadView._key_activate
    _open_attachment = ThreadView._open_attachment

    def __init__(self, cfg, m, elements, current):
        self.config = cfg
        self.focused_message = m
        self.state = {m: {"elements": elements, "current_element": current,
                          "expanded": True}}
        self.edit_mode = False
        self.toggled = 0

    def _key_toggle_expand(self, k):
        self.toggled += 1
        return True


def test_enter_on_attachment_saves_and_opens(tmp_path, monkeypatch):
    cfg = _cfg()
    # fake opener script capturing its argument
    capture = tmp_path / "opened_path"
    opener = tmp_path / "opener.sh"
    opener.write_text(f"#!/bin/sh\necho \"$1\" > {capture}\n")
    opener.chmod(0o755)
    cfg.config.put("attachment.external_open_cmd", str(opener))

    m = Message(filename=str(MAIL / "msg1.eml"))
    att = m.attachments()[0]

    from astroid_mail.modes.thread_view.page_client import Element
    elements = [Element("empty", -1, m.safe_mid()),
                Element("attachment", att.id, m.safe_mid())]
    stub = _ActStub(cfg, m, elements, current=1)

    procs = []
    real_popen = subprocess.Popen

    def spy_popen(argv, **kw):
        p = real_popen(argv, **kw)
        procs.append(argv)
        return p

    monkeypatch.setattr(subprocess, "Popen", spy_popen)

    assert stub._key_activate(None) is True
    assert stub.toggled == 0, "should open the attachment, not toggle expand"
    assert procs, "external opener was not spawned"
    argv = procs[0]
    assert argv[0] == str(opener)
    saved = Path(argv[1])
    assert saved.is_file(), "attachment was not saved before opening"
    assert saved.name == "signature.asc"
    assert saved.read_bytes() == att.contents()

    # wait for the opener to write its capture
    import time
    for _ in range(50):
        if capture.is_file():
            break
        time.sleep(0.05)
    assert capture.read_text().strip() == str(saved)


def test_enter_on_message_body_toggles_expand():
    cfg = _cfg()
    m = Message(filename=str(MAIL / "msg1.eml"))
    from astroid_mail.modes.thread_view.page_client import Element
    elements = [Element("empty", -1, m.safe_mid())]
    stub = _ActStub(cfg, m, elements, current=0)

    assert stub._key_activate(None) is True
    assert stub.toggled == 1


def test_enter_without_focused_message_is_noop():
    cfg = _cfg()
    stub = _ActStub(cfg, None, [], 0)
    stub.focused_message = None
    stub.state = {}
    assert stub._key_activate(None) is True
    assert stub.toggled == 0
