"""Spike (b): GMime 3.0 via GObject Introspection covers chunk.py needs."""

from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("GMime", "3.0")
from gi.repository import GMime, GLib  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MAIL = REPO_ROOT / "tests" / "mail" / "test_mail"


def parse(path: Path) -> GMime.Message:
    GMime.init()
    stream = GMime.StreamFile.open(str(path), "r")
    parser = GMime.Parser.new_with_stream(stream)
    return parser.construct_message(None)


def test_parse_simple_message():
    msg = parse(MAIL / "msg1.eml")
    assert msg is not None
    assert msg.get_subject() is not None
    sender = msg.get_from()
    assert sender.length() >= 1
    addr = sender.get_address(0)
    assert "@" in GMime.InternetAddressMailbox.get_addr(addr)


def test_walk_mime_tree():
    msg = parse(MAIL / "mime-message-no-content-type.eml")
    parts = []

    def walk(obj):
        parts.append(obj)
        if isinstance(obj, GMime.Multipart):
            for i in range(obj.get_count()):
                walk(obj.get_part(i))
        elif isinstance(obj, GMime.MessagePart):
            inner = obj.get_message()
            if inner:
                walk(inner.get_mime_part())

    walk(msg.get_mime_part())
    assert len(parts) >= 1
    for p in parts:
        ct = p.get_content_type()
        if ct is not None:
            assert isinstance(ct.get_mime_type(), str)


def test_decode_text_part_content():
    msg = parse(MAIL / "msg1.eml")
    part = msg.get_mime_part()
    # descend to first leaf part
    while isinstance(part, GMime.Multipart):
        part = part.get_part(0)
    assert isinstance(part, GMime.Part)

    # text parts: charset+encoding handled by GMime
    assert isinstance(part, GMime.TextPart)
    text = part.get_text()
    assert "experiment" in text

    # generic byte path (attachments): DataWrapper -> decoded stream
    content = part.get_content()
    out = GMime.StreamMem.new()
    n = content.write_to_stream(out)
    assert n > 0
    assert len(out.get_byte_array()) > 0  # returns bytes directly via gi


def test_address_list_parsing():
    al = GMime.InternetAddressList.parse(None, "Foo Bar <foo@bar.com>, baz@qux.org")
    assert al.length() == 2
    a0 = al.get_address(0)
    assert a0.get_name() == "Foo Bar"
    assert GMime.InternetAddressMailbox.get_addr(a0) == "foo@bar.com"


def test_message_construction():
    """compose.py direction: build a message and serialize it."""
    GMime.init()
    msg = GMime.Message.new(True)
    msg.add_mailbox(GMime.AddressType.FROM, "Me", "me@example.com")
    msg.add_mailbox(GMime.AddressType.TO, "You", "you@example.com")
    msg.set_subject("test subject", "UTF-8")
    msg.set_message_id("test.mid@example.com")

    part = GMime.Part.new_with_type("text", "plain")
    part.set_content(
        GMime.DataWrapper.new_with_stream(
            GMime.StreamMem.new_with_buffer(b"hello world\n"),
            GMime.ContentEncoding.DEFAULT))
    part.set_content_encoding(GMime.ContentEncoding.DEFAULT)
    msg.set_mime_part(part)

    out = GMime.StreamMem.new()
    msg.write_to_stream(GMime.FormatOptions.get_default(), out)
    raw = out.get_byte_array().decode()
    assert "Subject: test subject" in raw
    assert "hello world" in raw
    assert "Message-Id: <test.mid@example.com>" in raw
