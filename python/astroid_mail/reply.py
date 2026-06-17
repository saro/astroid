"""Helpers for ReplyMessage (port of src/modes/reply_message.cc).

Pure functions for: subject mangling ("Re: " prefix), quote-line +
body assembly, recipient derivation for each ReplyMode.

The GTK mode itself wraps these in EditMessage; tests exercise the
pure functions directly.
"""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path

from .account import Account, AccountManager
from .config import Config
from .models.message_thread import Message
from .quoting import format_quote_line, prefix_quote
from .utils.address import Address, AddressList
from .utils.dates import pretty_verbose_date


class ReplyMode(IntEnum):
    """Order matches the C++ enum -- the index drives the dropdown row."""
    Custom = 0
    Default = 1
    Sender = 2
    All = 3
    MailingList = 4


def reply_subject(orig_subject: str) -> str:
    """Add 'Re: ' unless one is already there."""
    s = (orig_subject or "").strip()
    if s.lower().startswith("re:"):
        return orig_subject
    return f"Re: {orig_subject}" if orig_subject else "Re: "


def reply_quote_body(config: Config, msg: Message) -> str:
    template = config.config.get_str(
        "mail.reply.quote_line", "Excerpts from %1's message of %2:")
    author = Address(msg.sender).fail_safe_name() if msg.sender else ""
    pretty = pretty_verbose_date(msg.time)

    line = format_quote_line(template, author, pretty, msg.time or 0)

    quote_proc = config.config.get_str(
        "mail.reply.quote_processor", "w3m -dump -T text/html")
    body = msg.quote(quote_processor=quote_proc)

    return line + "\n" + prefix_quote(body)


def _self_filter(al: AddressList, accounts: AccountManager) -> AddressList:
    """Return a new AddressList without addresses belonging to any of the
    configured accounts."""
    out = AddressList()
    seen = set()
    for a in al:
        if accounts.get_account_for_address(a.email()) is not None:
            continue
        key = a.email().lower()
        if key in seen:
            continue
        seen.add(key)
        out.addresses.append(a)
    return out


def _strip(al: AddressList, others: AddressList) -> AddressList:
    """Drop addresses that already appear in ``others`` (case-insensitive)."""
    skip = {a.email().lower() for a in others}
    out = AddressList()
    for a in al:
        if a.email().lower() in skip:
            continue
        out.addresses.append(a)
    return out


def _addrlist_str(al: AddressList) -> str:
    return ", ".join(a.full_address() for a in al)


def derive_recipients(mode: ReplyMode, msg: Message, accounts: AccountManager,
                      mailinglist_reply_to_sender: bool = True
                      ) -> tuple[str, str, str]:
    """Compute (to, cc, bcc) for the chosen ReplyMode.

    Port of src/modes/reply_message.cc:201-290.
    """
    sender = (msg.reply_to or msg.sender or "").strip()

    if mode == ReplyMode.Custom:
        return "", "", ""

    if mode in (ReplyMode.Default, ReplyMode.Sender):
        from_addr = Address(sender)
        # if sender is self, use the original To field instead
        if (accounts.get_account_for_address(from_addr.email()) is not None
                and msg.to()):
            to = _addrlist_str(_self_filter(AddressList(msg.to()), accounts))
        else:
            to = from_addr.full_address() if from_addr.email() else ""
        return to, "", ""

    if mode == ReplyMode.All:
        to_al = AddressList()
        from_addr = Address(sender)
        if (from_addr.email()
                and accounts.get_account_for_address(from_addr.email()) is None):
            to_al.addresses.append(from_addr)
        to_al.addresses.extend(_self_filter(AddressList(msg.to()), accounts))

        cc_al = _self_filter(AddressList(msg.cc()), accounts)
        cc_al = _strip(cc_al, to_al)

        bcc_al = _self_filter(AddressList(msg.bcc()), accounts)
        bcc_al = _strip(_strip(bcc_al, to_al), cc_al)

        return (_addrlist_str(to_al), _addrlist_str(cc_al),
                _addrlist_str(bcc_al))

    if mode == ReplyMode.MailingList:
        to_al = AddressList()
        # List-Post header sometimes wraps a mailto:; we want the address.
        lp = msg.list_post()
        if "<mailto:" in lp:
            inner = lp[lp.index("<mailto:") + len("<mailto:"):]
            inner = inner.split(">", 1)[0]
            to_al.addresses.append(Address(email=inner))
        to_al.addresses.extend(_self_filter(AddressList(msg.to()), accounts))
        if mailinglist_reply_to_sender and sender:
            from_addr = Address(sender)
            if accounts.get_account_for_address(from_addr.email()) is None:
                to_al.addresses.append(from_addr)

        cc_al = _strip(_self_filter(AddressList(msg.cc()), accounts), to_al)
        bcc_al = _strip(_strip(_self_filter(AddressList(msg.bcc()), accounts),
                               to_al), cc_al)
        return (_addrlist_str(to_al), _addrlist_str(cc_al),
                _addrlist_str(bcc_al))

    return "", "", ""


def references_for_reply(msg: Message) -> tuple[str, str]:
    """Return (References, In-Reply-To) for a reply to msg."""
    refs = (msg.references or "").strip()
    if refs:
        new_refs = f"{refs} <{msg.mid}>"
    else:
        new_refs = f"<{msg.mid}>"
    return new_refs, msg.mid
