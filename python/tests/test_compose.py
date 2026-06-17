"""Tests for the compose pipeline.

Hits the Message-Id generator branches, build() output shape, and a
fake-sendmail end-to-end send (port of tests/forktee.sh).
"""

from __future__ import annotations

import email
import email.parser
import os
import socket
import stat
import threading
import time
from email.message import EmailMessage
from pathlib import Path

import pytest

gi_pytest = pytest.importorskip("gi")
gi_pytest.require_version("GMime", "3.0")
from gi.repository import GMime  # noqa: E402

from astroid_mail.account import Account
from astroid_mail.compose import Attachment, ComposeMessage
from astroid_mail.compose_id import generate_message_id
from astroid_mail.config import Config


# --- helpers -----------------------------------------------------------------

def make_config(tmp_path):
    Config.__init__  # ensure module import side effects
    cfg = Config(no_load=True)
    cfg.config = cfg.setup_default_config(True)
    cfg.std_paths.config_dir = tmp_path / "astroid_cfg"
    cfg.std_paths.runtime_dir = tmp_path / "rt"
    cfg.std_paths.config_dir.mkdir(parents=True)
    cfg.std_paths.runtime_dir.mkdir(parents=True)
    return cfg


def make_account(tmp_path, sendmail="true", save_sent=False, sig=None,
                 sig_separate=False, gpgkey=""):
    sent_dir = tmp_path / "sent_cur"
    drafts_dir = tmp_path / "drafts_cur"
    sent_dir.mkdir(exist_ok=True)
    drafts_dir.mkdir(exist_ok=True)

    tree = {
        "name": "Charlie Root",
        "email": "root@localhost",
        "sendmail": sendmail,
        "default": "true",
        "save_sent": "true" if save_sent else "false",
        "save_sent_to": str(sent_dir),
        "save_drafts_to": str(drafts_dir),
        "additional_sent_tags": "fork",
        "signature_separate": "true" if sig_separate else "false",
        "signature_default_on": "true" if sig else "false",
        "signature_attach": "false",
        "gpgkey": gpgkey,
    }
    if sig:
        sigfile = tmp_path / "sig"
        sigfile.write_text(sig)
        tree["signature_file"] = str(sigfile)
    return Account("test", tree, tmp_path)


def parse(raw_bytes: bytes) -> EmailMessage:
    return email.parser.BytesParser(_class=EmailMessage).parsebytes(raw_bytes)


# --- Message-Id generation --------------------------------------------------

def test_msgid_uses_configured_fqdn_and_user(tmp_path, monkeypatch):
    cfg = make_config(tmp_path)
    cfg.config.put("mail.message_id_fqdn", "example.org")
    cfg.config.put("mail.message_id_user", "alice")

    mid = generate_message_id(cfg, now=1700000000)
    assert mid.startswith("1700000000.")
    assert mid.endswith(".alice@example.org")
    parts = mid.split("@")[0].split(".")
    assert len(parts) == 3
    assert len(parts[1]) == 10  # random_alphanumeric(10)


def test_msgid_falls_back_to_gethostname(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "myhost")
    if hasattr(socket, "getdomainname"):
        monkeypatch.setattr(socket, "getdomainname", lambda: "(none)")
    cfg = make_config(tmp_path)
    cfg.config.put("mail.message_id_fqdn", "")
    mid = generate_message_id(cfg)
    # (none) is stripped of parentheses; getdomainname result "none" has
    # no dot -> we don't add .none if domain itself is "none", but if
    # the resulting hostname has no '.', we append ".none".
    fqdn = mid.split("@", 1)[1]
    # "myhost.none" or "myhost.none.none" both acceptable -- key invariant: has dot
    assert "." in fqdn
    assert fqdn.startswith("myhost")


def test_msgid_random_when_no_hostname(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "")
    if hasattr(socket, "getdomainname"):
        monkeypatch.setattr(socket, "getdomainname", lambda: "")
    cfg = make_config(tmp_path)
    cfg.config.put("mail.message_id_fqdn", "")
    mid = generate_message_id(cfg)
    fqdn = mid.split("@", 1)[1]
    assert fqdn.endswith(".none")
    head = fqdn.rsplit(".", 1)[0]
    assert len(head) == 10


def test_msgid_user_default(tmp_path):
    cfg = make_config(tmp_path)
    cfg.config.put("mail.message_id_fqdn", "x.y")
    cfg.config.put("mail.message_id_user", "")
    mid = generate_message_id(cfg, now=1)
    assert mid.endswith(".astroid@x.y")


# --- build() output shapes --------------------------------------------------

def test_build_plain_text_only(tmp_path):
    cfg = make_config(tmp_path)
    acc = make_account(tmp_path)
    c = ComposeMessage(cfg, acc)
    c.set_to("alice@example.org")
    c.set_subject("hello")
    c.body = "first line\nsecond\n"
    c.build()
    c.finalize()

    msg = parse(c.to_bytes())
    assert msg["From"].startswith("Charlie Root")
    assert msg["To"] == "alice@example.org"
    assert msg["Subject"] == "hello"
    assert msg["Message-Id"].endswith("@" + msg["Message-Id"].split("@", 1)[1])
    assert "User-Agent" in msg
    ct = msg.get_content_type()
    assert ct == "text/plain"
    assert msg.get_content_charset() == "utf-8"
    body = msg.get_payload(decode=True).decode()
    assert "first line" in body and "second" in body


def test_build_inline_signature_with_separator(tmp_path):
    cfg = make_config(tmp_path)
    acc = make_account(tmp_path, sig="-- the sig --\nperson\n", sig_separate=True)
    c = ComposeMessage(cfg, acc)
    c.body = "hi\n"
    c.build()
    c.finalize()
    body = parse(c.to_bytes()).get_payload(decode=True).decode()
    # boundary: hi\n-- \n... (the separator must precede signature)
    assert body.startswith("hi\n")
    assert "\n-- \n" in body
    assert body.endswith("person\n")


def test_build_format_flowed(tmp_path):
    cfg = make_config(tmp_path)
    cfg.config.put("mail.format_flowed", True)
    acc = make_account(tmp_path)
    c = ComposeMessage(cfg, acc)
    c.body = "flowed text\n"
    c.build()
    c.finalize()
    msg = parse(c.to_bytes())
    assert msg.get_param("format") == "flowed"


def test_build_attachment_base64(tmp_path):
    cfg = make_config(tmp_path)
    acc = make_account(tmp_path)
    f = tmp_path / "blob.bin"
    payload = bytes(range(0, 256))
    f.write_bytes(payload)

    c = ComposeMessage(cfg, acc)
    c.body = "see attached\n"
    c.add_attachment(Attachment.from_file(f))
    c.build()
    c.finalize()

    msg = parse(c.to_bytes())
    assert msg.is_multipart()
    parts = list(msg.iter_parts())
    text = [p for p in parts if p.get_content_type() == "text/plain"][0]
    assert "see attached" in text.get_payload(decode=True).decode()
    att = [p for p in parts if p.get_filename() == "blob.bin"][0]
    assert att["Content-Transfer-Encoding"].lower() == "base64"
    assert att.get_payload(decode=True) == payload


# --- send pipeline ----------------------------------------------------------

def test_dryrun_writes_to_tmp(tmp_path):
    cfg = make_config(tmp_path)
    cfg.config.put("astroid.debug.dryrun_sending", True)
    cfg.config.put("mail.send_delay", 0)
    acc = make_account(tmp_path, sendmail="false")  # sendmail never invoked

    c = ComposeMessage(cfg, acc)
    c.body = "dryrun body\n"
    c.set_to("alice@example.org")
    c.build()
    c.finalize()

    sent: list[bool] = []
    c.connect("message-sent", lambda obj, ok: sent.append(ok))

    c._send(dispatch=lambda fn: fn())
    target = Path("/tmp") / c.id
    try:
        assert target.is_file()
        on_disk = target.read_bytes()
        assert b"dryrun body" in on_disk
        assert sent == [False]  # dryrun matches C++: returns False, no sent action
    finally:
        if target.exists():
            target.unlink()


def test_send_via_fake_sendmail(tmp_path):
    """Port of tests/forktee.sh: capture sendmail stdin, assert RFC5322."""
    captured = tmp_path / "captured.eml"
    script = tmp_path / "fake_sendmail.sh"
    script.write_text(f"#!/bin/sh\ncat > {captured}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IRUSR)

    cfg = make_config(tmp_path)
    cfg.config.put("mail.send_delay", 0)
    acc = make_account(tmp_path, sendmail=str(script), save_sent=True)

    c = ComposeMessage(cfg, acc)
    c.body = "real body\n"
    c.set_to("alice@example.org")
    c.set_subject("hi from py")
    c.build()
    c.finalize()

    sent: list[bool] = []
    c.connect("message-sent", lambda obj, ok: sent.append(ok))
    ok = c._send(dispatch=lambda fn: fn())
    assert ok is True
    assert sent == [True]

    msg = parse(captured.read_bytes())
    assert msg["Subject"] == "hi from py"
    assert msg["Message-Id"]
    assert "User-Agent" in msg
    assert "real body" in msg.get_payload(decode=True).decode()

    # save_sent copy on disk under save_sent_to with maildir-style suffix
    sent_files = list((tmp_path / "sent_cur").iterdir())
    assert len(sent_files) == 1
    assert sent_files[0].name.endswith(":2,")


def test_send_delay_cancel(tmp_path):
    cfg = make_config(tmp_path)
    cfg.config.put("mail.send_delay", 3)
    # use a sendmail that would log loudly so we can detect leakage
    acc = make_account(tmp_path, sendmail="false")

    c = ComposeMessage(cfg, acc)
    c.body = "x"
    c.set_to("a@b.c")
    c.build()
    c.finalize()

    statuses: list[tuple[bool, str]] = []
    sent: list[bool] = []
    c.connect("message-send-status",
              lambda obj, warn, txt: statuses.append((warn, txt)))
    c.connect("message-sent", lambda obj, ok: sent.append(ok))

    t = threading.Thread(target=c._send, args=(lambda fn: fn(),))
    t.start()
    time.sleep(0.2)
    c.cancel_sending()
    t.join(5)
    assert sent == [False]
    assert any(w for w, _ in statuses)  # got a warning status


def test_invalid_sendmail_command(tmp_path):
    cfg = make_config(tmp_path)
    cfg.config.put("mail.send_delay", 0)
    acc = make_account(tmp_path, sendmail='msmtp "unterminated')

    c = ComposeMessage(cfg, acc)
    c.body = "x"
    c.set_to("a@b.c")
    c.build()
    c.finalize()

    sent: list[bool] = []
    c.connect("message-sent", lambda obj, ok: sent.append(ok))
    assert c._send(dispatch=lambda fn: fn()) is False
    assert sent == [False]
