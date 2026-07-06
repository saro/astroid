"""Port of src/crypto.cc — GPG via the GMime crypto context.

Reading: ``decrypt_and_verify`` unwraps multipart/encrypted and
``verify_signature`` checks multipart/signed, collecting human-readable
signature / recipient descriptions for the thread view.

Writing: ``sign`` and ``encrypt`` wrap an entity for ComposeMessage.

Config: ``crypto.gpg.enabled`` and ``crypto.gpg.always_trust`` are
honoured. (GMime 3 always runs the ``gpg`` found in PATH — the
``crypto.gpg.path`` option of the C++/GMime2 era cannot be set through
introspection.)
"""

from __future__ import annotations

import re

import gi

gi.require_version("GMime", "3.0")
from gi.repository import GLib, GMime  # noqa: E402

from .log import log
from .utils.address import Address, AddressList

GMime.init()

_SUPPORTED = ("application/pgp-encrypted", "application/pgp-signature")


def _cert_info(ce) -> dict:
    if ce is None:
        return {"name": "", "email": "", "key": "", "fingerprint": "",
                "trust": "unknown"}
    trust_names = {
        GMime.Trust.UNKNOWN: "unknown",
        GMime.Trust.UNDEFINED: "undefined",
        GMime.Trust.NEVER: "never",
        GMime.Trust.MARGINAL: "marginal",
        GMime.Trust.FULL: "full",
        GMime.Trust.ULTIMATE: "ultimate",
    }
    return {
        "name": ce.get_name() or "",
        "email": ce.get_email() or "",
        "key": ce.get_key_id() or "",
        "fingerprint": ce.get_fingerprint() or "",
        "trust": trust_names.get(ce.get_trust(), "unknown"),
    }


_SIG_ERRORS = (
    (int(GMime.SignatureStatus.KEY_REVOKED), "revoked-key"),
    (int(GMime.SignatureStatus.KEY_EXPIRED), "expired-key"),
    (int(GMime.SignatureStatus.SIG_EXPIRED), "expired-sig"),
    (int(GMime.SignatureStatus.KEY_MISSING), "key-missing"),
    (int(GMime.SignatureStatus.CRL_MISSING), "crl-missing"),
    (int(GMime.SignatureStatus.CRL_TOO_OLD), "crl-too-old"),
    (int(GMime.SignatureStatus.BAD_POLICY), "bad-policy"),
    (int(GMime.SignatureStatus.SYS_ERROR), "sys-error"),
    (int(GMime.SignatureStatus.TOFU_CONFLICT), "tofu-conflict"),
)

_RED = int(GMime.SignatureStatus.RED)

# GMime < 3.2.14 mis-annotates CertificateList.get_certificate() as
# (transfer full) while the C function transfers nothing: PyGObject then
# unrefs a reference it never got and the certificate is double-freed at
# GC time (hard segfault). Balance the books with one manual g_object_ref
# on affected versions (fixed upstream in 3.2.14).
_NEEDS_CERT_PIN = not GMime.check_version(3, 2, 14)

if _NEEDS_CERT_PIN:
    import ctypes
    import ctypes.util

    _gobject = ctypes.CDLL(ctypes.util.find_library("gobject-2.0")
                           or "libgobject-2.0.so.0")
    _gobject.g_object_ref.restype = ctypes.c_void_p
    _gobject.g_object_ref.argtypes = [ctypes.c_void_p]

    _PTR_RE = re.compile(r"at 0x([0-9a-fA-F]+)\)>?\s*$")

    def _pin_cert(obj) -> None:
        """Add the reference the wrong annotation makes PyGObject drop."""
        if obj is None:
            return
        m = _PTR_RE.search(repr(obj))
        if m:
            _gobject.g_object_ref(ctypes.c_void_p(int(m.group(1), 16)))
else:
    def _pin_cert(obj) -> None:
        pass


def _sig_status(s) -> int:
    """GMimeSignatureStatus is a flags type but the typelib declares it as
    an enum, so PyGObject raises ValueError on combined values (e.g.
    VALID|GREEN == 3). The raw value is recoverable from the message."""
    try:
        return int(s.get_status())
    except ValueError as e:
        m = re.search(r"invalid enum value: (\d+)", str(e))
        if m:
            return int(m.group(1))
        log.error("crypto: cannot read signature status: %s", e)
        return _RED  # fail closed: treat unreadable status as bad


class Crypto:
    def __init__(self, config, protocol: str = "application/pgp-encrypted"):
        cfg = config.config
        self.gpgenabled = cfg.get_bool("crypto.gpg.enabled")
        self.always_trust = cfg.get_bool("crypto.gpg.always_trust")

        self.protocol = (protocol or "").lower()
        self.ready = False

        self.decrypted = False
        self.decrypt_tried = False
        self.decrypt_error = ""
        self.verify_tried = False
        self.verified = False

        # human-readable summaries for the thread view
        self.sign_strings: list[str] = []
        self.sig_errors: list[str] = []
        self.enc_strings: list[str] = []

        if self.protocol not in _SUPPORTED:
            log.error("crypto: unsupported protocol: %s", self.protocol)
            return
        if not self.gpgenabled:
            log.warning("crypto: gpg is disabled in the config")
            return

        try:
            self.ctx = GMime.GpgContext.new()
            self.ready = True
        except Exception as e:
            log.error("crypto: could not create gpg context: %s", e)

    # -- reading ---------------------------------------------------------------

    def decrypt_and_verify(self, part):
        """Decrypt a GMime.MultipartEncrypted; returns the decrypted
        GMime.Object or None. Signature state is collected if the message
        was signed+encrypted."""
        self.decrypt_tried = True

        if not self.ready or not isinstance(part, GMime.MultipartEncrypted):
            log.error("crypto: cannot decrypt: not a multipart/encrypted")
            return None

        try:
            # session_key must be "" — the gi annotation rejects None
            dp, result = part.decrypt(GMime.DecryptFlags.NONE, "")
        except GLib.Error as e:
            log.error("crypto: failed to decrypt message: %s", e.message)
            self.decrypt_error = e.message
            self.decrypted = False
            return None

        self.decrypted = dp is not None
        if self.decrypted:
            log.info("crypto: successfully decrypted message.")

        if result is not None:
            rlist = result.get_recipients()
            if rlist is not None:
                for i in range(rlist.length()):
                    ce = rlist.get_certificate(i)
                    _pin_cert(ce)
                    info = _cert_info(ce)
                    self.enc_strings.append(
                        "Encrypted for: %s (%s) [0x%s]" % (
                            info["name"], info["email"], info["key"]))
            slist = result.get_signatures()
            if slist is not None and slist.length() > 0:
                self.verify_tried = True
                self.verified = self._collect_signatures(slist)

        return dp

    def verify_signature(self, part) -> bool:
        """Verify a GMime.MultipartSigned; populates sign_strings."""
        self.verify_tried = True

        if not self.ready or not isinstance(part, GMime.MultipartSigned):
            log.error("crypto: cannot verify: not a multipart/signed")
            return False

        try:
            slist = part.verify(GMime.VerifyFlags.NONE)
        except GLib.Error as e:
            log.error("crypto: verify failed: %s", e.message)
            self.sig_errors.append(str(e.message))
            self.verified = False
            return False

        self.verified = self._collect_signatures(slist)
        return self.verified

    def _collect_signatures(self, slist) -> bool:
        if slist is None or slist.length() == 0:
            return False

        all_good = True
        for i in range(slist.length()):
            s = slist.get_signature(i)
            status = _sig_status(s)

            # port of g_mime_signature_status_good(): no RED and no error bits
            errors = [name for bit, name in _SIG_ERRORS if status & bit]
            red = bool(status & _RED)
            good = not red and not errors

            all_good &= good

            info = _cert_info(s.get_certificate())
            state = "Good" if good else ("Bad" if red else "Erroneous")
            err = f" [Error: {','.join(errors)}]" if errors else ""

            self.sign_strings.append(
                "%s signature from: %s (%s) [0x%s] [trust: %s]%s" % (
                    state, info["name"], info["email"], info["key"],
                    info["trust"], err))
            self.sig_errors.extend(errors)

        return all_good

    # -- writing ---------------------------------------------------------------

    def sign(self, entity, userid: str):
        """Wrap entity in multipart/signed. Returns the wrapper or None."""
        if not self.ready:
            return None, "gpg not available"
        try:
            out = GMime.MultipartSigned.sign(self.ctx, entity, userid)
            return out, ""
        except GLib.Error as e:
            log.error("crypto: signing failed: %s", e.message)
            return None, e.message

    def encrypt(self, entity, sign: bool, userid: str,
                from_addr: str, recipients_raw: str):
        """Wrap entity in multipart/encrypted for all recipients + self.
        Returns (wrapper|None, error)."""
        if not self.ready:
            return None, "gpg not available"

        recipients: list[str] = []
        seen = set()
        for raw in (recipients_raw, from_addr):
            for a in AddressList(raw):
                em = a.email().lower()
                if em and em not in seen:
                    seen.add(em)
                    recipients.append(a.email())
        if from_addr and not recipients:
            fa = Address(from_addr)
            if fa.email():
                recipients.append(fa.email())

        log.debug("crypto: encrypting for: %s", ", ".join(recipients))

        flags = (GMime.EncryptFlags.ALWAYS_TRUST if self.always_trust
                 else GMime.EncryptFlags.NONE)
        try:
            out = GMime.MultipartEncrypted.encrypt(
                self.ctx, entity, sign, userid or None, flags, recipients)
            return out, ""
        except GLib.Error as e:
            log.error("crypto: encryption failed: %s", e.message)
            return None, e.message
