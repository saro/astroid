"""Sending always asks for confirmation first (EditMessage._do_send)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")

from astroid_mail.modes.edit_message import EditMessage  # noqa: E402


class _Stub:
    """Minimal stand-in exercising EditMessage._do_send without GTK."""
    def __init__(self, to="alice@example.org"):
        self.sending = False
        self.compose = SimpleNamespace(to=to)
        self.asked = []          # (question, closure)
        self.sent = 0

    # bound methods under test
    _do_send = EditMessage._do_send

    # collaborators
    def _sync_compose_from_ui(self):
        pass

    def ask_yes_no(self, question, closure):
        self.asked.append((question, closure))

    def _send_now(self):
        self.sent += 1


def test_send_asks_confirmation_and_sends_on_yes():
    s = _Stub("bob@example.org")
    s._do_send()

    assert len(s.asked) == 1
    question, closure = s.asked[0]
    assert "bob@example.org" in question
    assert s.sent == 0           # nothing sent yet

    closure(True)                # user confirms
    assert s.sent == 1


def test_send_does_not_send_on_no():
    s = _Stub("bob@example.org")
    s._do_send()
    _q, closure = s.asked[0]
    closure(False)               # user declines
    assert s.sent == 0


def test_no_recipient_prompts_send_anyway():
    s = _Stub("")
    s._do_send()
    assert len(s.asked) == 1
    question, closure = s.asked[0]
    assert "No recipient" in question
    closure(True)
    assert s.sent == 1


def test_no_recipient_decline_does_nothing():
    s = _Stub("   ")
    s._do_send()
    _q, closure = s.asked[0]
    closure(False)
    assert s.sent == 0


def test_send_ignored_while_already_sending():
    s = _Stub("bob@example.org")
    s.sending = True
    s._do_send()
    assert s.asked == []
    assert s.sent == 0
