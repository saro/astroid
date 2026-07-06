"""GPG: sign/verify and encrypt/decrypt round trips (port of test_crypto.cc).

Generates throwaway keys in an isolated GNUPGHOME; skipped when gpg is
not installed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
gi.require_version("GMime", "3.0")
from gi.repository import GMime  # noqa: E402

from astroid_mail.account import Account  # noqa: E402
from astroid_mail.compose import ComposeMessage  # noqa: E402
from astroid_mail.config import Config  # noqa: E402
from astroid_mail.crypto import Crypto  # noqa: E402
from astroid_mail.models.chunk import Chunk  # noqa: E402
from astroid_mail.models.message_thread import Message  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("gpg") is None,
                                reason="gpg not installed")

SELF_EMAIL = "gaute@astroidmail.bar"
OTHER_EMAIL = "other@astroidmail.bar"

KEY_SPEC = """\
%echo generating test key
Key-Type: RSA
Key-Length: 2048
Name-Real: {name}
Name-Email: {email}
Expire-Date: 0
%no-protection
%commit
%echo done
"""


@pytest.fixture(scope="module")
def gpg_home(tmp_path_factory):
    home = tmp_path_factory.mktemp("gnupg")
    home.chmod(0o700)
    env = dict(os.environ, GNUPGHOME=str(home))

    for name, email in (("Tester1", SELF_EMAIL), ("Tester2", OTHER_EMAIL)):
        spec = home / f"{name}.spec"
        spec.write_text(KEY_SPEC.format(name=name, email=email))
        r = subprocess.run(["gpg", "--batch", "--gen-key", str(spec)],
                           env=env, capture_output=True, text=True)
        if r.returncode != 0:
            pytest.skip(f"gpg key generation failed: {r.stderr.strip()}")

    old = os.environ.get("GNUPGHOME")
    os.environ["GNUPGHOME"] = str(home)
    yield home
    subprocess.run(["gpgconf", "--kill", "all"], env=env, capture_output=True)
    if old is None:
        os.environ.pop("GNUPGHOME", None)
    else:
        os.environ["GNUPGHOME"] = old


def _cfg():
    c = Config(no_load=True)
    c.config = c.setup_default_config(True)
    return c


def _account(tmp_path, gpgkey=SELF_EMAIL):
    return Account("t", {
        "name": "Gaute Tester", "email": SELF_EMAIL,
        "sendmail": "true", "default": "true",
        "gpgkey": gpgkey,
    }, tmp_path)


def _compose(tmp_path, cfg, *, sign=False, encrypt=False, to=SELF_EMAIL):
    c = ComposeMessage(cfg, _account(tmp_path))
    c.set_to(to)
    c.set_subject("crypto test")
    c.body = "very secret business\n"
    c.include_signature = False
    c.sign = sign
    c.encrypt = encrypt
    c.build()
    c.finalize()
    return c


def _parse_top(raw: bytes):
    """Returns (message, top_part). The message MUST be kept alive while
    the part is used: get_mime_part() is transfer-none."""
    GMime.init()
    stream = GMime.StreamMem.new_with_buffer(raw)
    parser = GMime.Parser.new_with_stream(stream)
    msg = parser.construct_message(None)
    assert msg is not None
    return msg, msg.get_mime_part()


def test_sign_and_verify_roundtrip(gpg_home, tmp_path):
    cfg = _cfg()
    c = _compose(tmp_path, cfg, sign=True)
    assert c.encryption_success, c.encryption_error

    _msg, top = _parse_top(c.to_bytes())
    assert isinstance(top, GMime.MultipartSigned)

    cy = Crypto(cfg, "application/pgp-signature")
    assert cy.ready
    assert cy.verify_signature(top) is True
    assert cy.verified
    assert any("Good signature" in s or s.startswith("Good")
               for s in cy.sign_strings), cy.sign_strings
    assert SELF_EMAIL in " ".join(cy.sign_strings)


def test_encrypt_and_decrypt_roundtrip(gpg_home, tmp_path):
    cfg = _cfg()
    c = _compose(tmp_path, cfg, encrypt=True)
    assert c.encryption_success, c.encryption_error

    _msg, top = _parse_top(c.to_bytes())
    assert isinstance(top, GMime.MultipartEncrypted)

    cy = Crypto(cfg, "application/pgp-encrypted")
    dp = cy.decrypt_and_verify(top)
    assert cy.decrypted
    assert dp is not None
    # decrypted content contains our body
    out = GMime.StreamMem.new()
    dp.write_to_stream(GMime.FormatOptions.get_default(), out)
    assert b"very secret business" in bytes(out.get_byte_array())


def test_encrypt_sign_and_chunk_parse(gpg_home, tmp_path):
    """Full read-path: an encrypted+signed message parsed through Chunk
    decrypts transparently and reports both states."""
    cfg = _cfg()
    c = _compose(tmp_path, cfg, sign=True, encrypt=True)
    assert c.encryption_success, c.encryption_error

    eml = tmp_path / "enc.eml"
    eml.write_bytes(c.to_bytes())

    Chunk.config = cfg
    try:
        m = Message(filename=str(eml))
        assert m.root is not None
        assert m.root.isencrypted
        assert m.root.crypt is not None
        assert m.root.crypt.decrypted
        assert m.root.crypt.verify_tried
        assert m.root.crypt.verified

        # the decrypted body is viewable through the normal part machinery
        text = m.plain_text()
        assert "very secret business" in text
    finally:
        Chunk.config = None


def test_encrypt_to_unknown_recipient_fails(gpg_home, tmp_path):
    cfg = _cfg()
    c = _compose(tmp_path, cfg, encrypt=True, to="nobody@nowhere.example")
    assert c.encryption_success is False
    assert c.encryption_error


def test_signed_message_chunk_parse(gpg_home, tmp_path):
    cfg = _cfg()
    c = _compose(tmp_path, cfg, sign=True)
    eml = tmp_path / "signed.eml"
    eml.write_bytes(c.to_bytes())

    Chunk.config = cfg
    try:
        m = Message(filename=str(eml))
        assert m.root is not None
        assert m.root.issigned
        assert m.root.crypt.verified
        assert "very secret business" in m.plain_text()
    finally:
        Chunk.config = None


def test_crypto_disabled_in_config(gpg_home, tmp_path):
    cfg = _cfg()
    cfg.config.put("crypto.gpg.enabled", False)
    cy = Crypto(cfg, "application/pgp-encrypted")
    assert not cy.ready
