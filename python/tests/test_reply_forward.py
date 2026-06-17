"""Reply / forward semantics tests against the C++ test corpus."""

from __future__ import annotations

from pathlib import Path

import pytest

gi_pytest = pytest.importorskip("gi")
gi_pytest.require_version("GMime", "3.0")
from gi.repository import GMime  # noqa: E402

from astroid_mail.account import AccountManager
from astroid_mail.config import Config
from astroid_mail.forward import (
    FwdDisposition, forward_as_attachment, forward_inline_body, forward_subject,
    forward_attachments, resolve_disposition,
)
from astroid_mail.models.message_thread import Message
from astroid_mail.quoting import format_quote_line, prefix_quote
from astroid_mail.reply import (
    ReplyMode, derive_recipients, references_for_reply, reply_quote_body,
    reply_subject,
)
from astroid_mail.utils.address import AddressList

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MAIL = REPO_ROOT / "tests" / "mail" / "test_mail"


# --- helpers ---

class _FakeConfig:
    """Just enough of Config for the helpers."""
    def __init__(self, **kv):
        cfg = Config(no_load=True)
        cfg.config = cfg.setup_default_config(True)
        for k, v in kv.items():
            cfg.config.put(k, v)
        self.config = cfg.config
        self.std_paths = cfg.std_paths


def cfg(**overrides):
    c = Config(no_load=True)
    c.config = c.setup_default_config(True)
    for k, v in overrides.items():
        c.config.put(k, v)
    return c


def accounts_with(*emails):
    c = cfg()
    # build an accounts.* tree, then load it
    for i, em in enumerate(emails):
        prefix = f"accounts.me{i}"
        c.config.put(f"{prefix}.email", em)
        c.config.put(f"{prefix}.name", f"Me {i}")
        c.config.put(f"{prefix}.sendmail", "true")
        c.config.put(f"{prefix}.default", "true" if i == 0 else "false")
    return AccountManager(c)


def make_msg(eml: Path) -> Message:
    return Message(filename=str(eml))


# --- quoting primitives -----------------------------------------------------

def test_prefix_quote_space_rule():
    src = "alpha\nbeta\n> already quoted\n\n"
    out = prefix_quote(src)
    assert out == "> alpha\n> beta\n>> already quoted\n> \n"


def test_prefix_quote_no_trailing_newline():
    assert prefix_quote("one\ntwo") == "> one\n> two"


def test_prefix_quote_empty():
    assert prefix_quote("") == ""


def test_format_quote_line_substitution():
    out = format_quote_line(
        "Excerpts from %1's message of %2:",
        "Charlie Root", "Thu, 03 Aug 2017 12:00:00 +0000", 1501761600)
    assert "Charlie Root" in out
    assert "Thu, 03 Aug 2017 12:00:00 +0000" in out


def test_format_quote_line_strftime_escape():
    """Glib.DateTime.format expands %Y/%m etc. in the configured line."""
    out = format_quote_line(
        "On %Y-%m-%d, %1 wrote:", "Foo", "ignored", 1500000000)
    assert "Foo" in out
    # the %Y/%m/%d expanded to digits
    import datetime
    expected = datetime.datetime.fromtimestamp(1500000000).strftime("%Y-%m-%d")
    assert expected in out


def test_format_quote_line_escapes_percent_in_author():
    out = format_quote_line("by %1:", "100% sure", "ignored", 1500000000)
    assert "100% sure" in out
    # no double % left over
    assert "%%" not in out


# --- reply mode recipient derivation ----------------------------------------

class _M:
    """Synthetic Message-like for recipient-derivation tests."""
    def __init__(self, sender, to="", cc="", bcc="", reply_to="",
                 list_post=None):
        self.sender = sender
        self.reply_to = reply_to
        self._to = to
        self._cc = cc
        self._bcc = bcc
        self._lp = list_post or ""
        self.mid = "m@e"
        self.references = ""
        self.time = 0

    def to(self):
        return self._to

    def cc(self):
        return self._cc

    def bcc(self):
        return self._bcc

    def list_post(self):
        return self._lp

    def is_list_post(self):
        return bool(self._lp)


def test_reply_default_to_sender():
    accts = accounts_with("me@x.com")
    msg = _M("Alice <alice@x.com>", to="me@x.com, bob@x.com")
    to, cc, bcc = derive_recipients(ReplyMode.Default, msg, accts)
    assert to == "Alice <alice@x.com>"
    assert cc == "" and bcc == ""


def test_reply_default_when_self_is_sender():
    accts = accounts_with("me@x.com")
    msg = _M("Me <me@x.com>", to="alice@x.com, bob@x.com")
    to, cc, bcc = derive_recipients(ReplyMode.Default, msg, accts)
    # self loop: use the original To
    assert "alice@x.com" in to and "bob@x.com" in to
    assert cc == "" and bcc == ""


def test_reply_default_uses_reply_to_when_present():
    accts = accounts_with("me@x.com")
    msg = _M("Alice <alice@x.com>", to="me@x.com",
             reply_to="List <list@x.com>")
    to, _, _ = derive_recipients(ReplyMode.Default, msg, accts)
    assert "list@x.com" in to
    assert "alice@x.com" not in to


def test_reply_all_dedupes_and_strips_self():
    accts = accounts_with("me@x.com")
    msg = _M("Alice <alice@x.com>",
             to="me@x.com, bob@x.com",
             cc="carol@x.com, alice@x.com, bob@x.com",
             bcc="dave@x.com, me@x.com")
    to, cc, bcc = derive_recipients(ReplyMode.All, msg, accts)
    to_emails = {a.email() for a in AddressList(to)}
    cc_emails = {a.email() for a in AddressList(cc)}
    bcc_emails = {a.email() for a in AddressList(bcc)}
    assert "alice@x.com" in to_emails and "bob@x.com" in to_emails
    assert "me@x.com" not in to_emails | cc_emails | bcc_emails
    # cc minus to: alice/bob already in to
    assert cc_emails == {"carol@x.com"}
    # bcc minus to minus cc
    assert bcc_emails == {"dave@x.com"}


def test_reply_mailinglist_to_includes_list_post():
    accts = accounts_with("me@x.com")
    msg = _M("Alice <alice@x.com>", to="me@x.com",
             list_post="<mailto:list@example.org>")
    to, _, _ = derive_recipients(
        ReplyMode.MailingList, msg, accts,
        mailinglist_reply_to_sender=False)
    assert "list@example.org" in to
    assert "alice@x.com" not in to


def test_reply_mailinglist_with_sender_when_enabled():
    accts = accounts_with("me@x.com")
    msg = _M("Alice <alice@x.com>", to="me@x.com",
             list_post="<mailto:list@example.org>")
    to, _, _ = derive_recipients(
        ReplyMode.MailingList, msg, accts,
        mailinglist_reply_to_sender=True)
    assert "list@example.org" in to and "alice@x.com" in to


def test_reply_subject_dedupes_re_prefix():
    assert reply_subject("hello") == "Re: hello"
    assert reply_subject("Re: hello") == "Re: hello"
    assert reply_subject("") == "Re: "


def test_references_assembly():
    msg = _M("a@b.c", to="")
    msg.mid = "abc@def"
    refs, irt = references_for_reply(msg)
    assert refs == "<abc@def>"
    assert irt == "abc@def"

    msg.references = "<x@y> <a@b>"
    refs, _ = references_for_reply(msg)
    assert refs == "<x@y> <a@b> <abc@def>"


# --- end-to-end against a fixture eml ---------------------------------------

def test_reply_quote_body_against_msg1():
    eml = MAIL / "msg1.eml"
    msg = make_msg(eml)
    c = cfg()
    body = reply_quote_body(c, msg)

    # quote line (default template) appears as line 1
    first = body.splitlines()[0]
    assert "Excerpts from" in first
    # body lines are > -prefixed
    lines = body.splitlines()[1:]
    # find a content line
    content_line = next(l for l in lines if "experiment" in l)
    assert content_line.startswith("> ")


# --- forward ----------------------------------------------------------------

def test_resolve_disposition():
    c = cfg()
    assert resolve_disposition(c, FwdDisposition.Inline) == FwdDisposition.Inline
    assert resolve_disposition(c, FwdDisposition.Default) == FwdDisposition.Inline
    c.config.put("mail.forward.disposition", "attachment")
    assert resolve_disposition(c, FwdDisposition.Default) == FwdDisposition.Attach


def test_forward_subject_dedupes():
    assert forward_subject("hi") == "Fwd: hi"
    assert forward_subject("Fwd: hi") == "Fwd: hi"


def test_forward_inline_body_has_header_block():
    msg = make_msg(MAIL / "msg1.eml")
    c = cfg()
    body = forward_inline_body(c, msg)
    assert "Forwarding" in body
    assert "From:" in body and "Date:" in body and "Subject:" in body


def test_forward_as_attachment_wraps_eml():
    msg = make_msg(MAIL / "msg1.eml")
    att = forward_as_attachment(msg)
    assert att.is_mime_message
    assert att.content_type == "message/rfc822"
    assert att.valid
