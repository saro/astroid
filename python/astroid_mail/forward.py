"""Helpers for ForwardMessage (port of src/modes/forward_message.cc)."""

from __future__ import annotations

from enum import IntEnum

from .compose import Attachment
from .config import Config
from .models.message_thread import Message
from .quoting import format_quote_line
from .utils.address import Address, AddressList
from .utils.dates import pretty_verbose_date


class FwdDisposition(IntEnum):
    Default = 0    # follow mail.forward.disposition
    Inline = 1
    Attach = 2


def forward_subject(orig_subject: str) -> str:
    s = (orig_subject or "").strip()
    if s.lower().startswith("fwd:"):
        return orig_subject
    return f"Fwd: {orig_subject}" if orig_subject else "Fwd: "


def resolve_disposition(config: Config,
                        disp: FwdDisposition) -> FwdDisposition:
    if disp != FwdDisposition.Default:
        return disp
    if config.config.get_str("mail.forward.disposition") == "attachment":
        return FwdDisposition.Attach
    return FwdDisposition.Inline


def forward_inline_body(config: Config, msg: Message) -> str:
    template = config.config.get_str(
        "mail.forward.quote_line", "Forwarding %1's message of %2:")
    author = Address(msg.sender).fail_safe_name() if msg.sender else ""
    pretty = pretty_verbose_date(msg.time)

    line = format_quote_line(template, author, pretty, msg.time or 0)

    parts = [line, ""]
    parts.append(f"From: {msg.sender}")
    parts.append(f"Date: {pretty}")
    parts.append(f"Subject: {msg.subject}")
    parts.append(f"To: {', '.join(a.full_address() for a in AddressList(msg.to()))}")
    cc_list = AddressList(msg.cc())
    if len(cc_list) > 0:
        parts.append(f"Cc: {', '.join(a.full_address() for a in cc_list)}")
    parts.append("")

    quote_proc = config.config.get_str(
        "mail.reply.quote_processor", "w3m -dump -T text/html")
    parts.append(msg.quote(quote_processor=quote_proc))
    return "\n".join(parts)


def forward_attachments(msg: Message) -> list[Attachment]:
    """Carry over the original attachments to an inline forward."""
    return [Attachment.from_chunk(c) for c in msg.attachments()]


def forward_as_attachment(msg: Message) -> Attachment:
    return Attachment.from_message(msg)
