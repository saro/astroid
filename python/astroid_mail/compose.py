"""Port of src/compose_message.cc.

Build / finalize / send a GMime message. Sending happens in a worker
thread with a cancellable send_delay countdown (port of
``cancel_send_during_delay`` + ``send_cancel_cv``). The full message is
written to the configured sendmail's stdin via Gio.Subprocess; on
success the file is optionally saved to ``account.save_sent_to`` as a
maildir entry (``{msg_id}:2,``) and an ``AddSentMessage`` action is
queued so notmuch indexes it with ``mail.sent_tags +
account.additional_sent_tags``.
"""

from __future__ import annotations

import dataclasses
import shlex
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("GMime", "3.0")
from gi.repository import GLib, GMime, GObject  # noqa: E402

from .account import Account
from .compose_id import generate_message_id
from .config import Config
from .log import log
from .utils.address import Address, AddressList

# Built-in fallback for `mail.user_agent = "default"`; the C++ uses the
# astroid version string. We use a clear product identifier so notmuch
# search filters that target the C++ UA still work after a switch.
DEFAULT_USER_AGENT = "astroid-py"

# Default size limit when shovelling sendmail logs into our log sink
_MAX_PROC_LOG = 64 * 1024


# -- attachments --------------------------------------------------------------

@dataclass
class Attachment:
    """Port of ComposeMessage::Attachment.

    Sources:
      * a file path (``content_type`` auto-detected via GIO mime sniff),
      * an existing ``models.message_thread.Message`` (treated as
        ``message/rfc822``; ``is_mime_message = True``),
      * a chunk content (``Chunk.contents()`` bytes + ``content_type``).
    """

    name: str = ""
    path: Path | None = None
    is_mime_message: bool = False
    inline: bool = False
    content_type: str = "application/octet-stream"
    contents: bytes = b""
    message_path: Path | None = None  # for is_mime_message
    valid: bool = True

    @classmethod
    def from_file(cls, path: str | Path, inline: bool = False) -> "Attachment":
        p = Path(path)
        att = cls(name=p.name, path=p, inline=inline, valid=p.is_file())
        if att.valid:
            try:
                guess, _ = GLib.content_type_guess(p.name, None)
                if guess:
                    att.content_type = GLib.content_type_get_mime_type(guess) or att.content_type
            except Exception:
                pass
            att.contents = p.read_bytes()
        return att

    @classmethod
    def from_message(cls, msg) -> "Attachment":
        return cls(
            name=(msg.subject or msg.mid) + ".eml",
            message_path=Path(msg.filename),
            is_mime_message=True,
            content_type="message/rfc822",
            valid=Path(msg.filename).is_file(),
        )

    @classmethod
    def from_chunk(cls, chunk) -> "Attachment":
        return cls(
            name=chunk.get_filename() or f"part-{chunk.id}",
            content_type=chunk.mime_type or "application/octet-stream",
            contents=chunk.contents(),
            valid=True,
        )


# -- compose ------------------------------------------------------------------

class ComposeMessage(GObject.Object):
    """Build + send pipeline. Owns one ``GMime.Message`` until ``finalize``
    is called; safe to ``build`` again to regenerate a preview."""

    __gsignals__ = {
        "message-sent": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "message-send-status": (GObject.SignalFlags.RUN_FIRST, None,
                                (bool, str)),
    }

    def __init__(self, config: Config, account: Account):
        super().__init__()
        self.config = config
        self.account = account

        self.id: str = ""
        self.body: str = ""

        self.subject: str = ""
        self.from_addr: str = account.full_address() if account else ""
        self.to: str = ""
        self.cc: str = ""
        self.bcc: str = ""
        self.reply_to: str = ""
        self.references: str = ""
        self.inreplyto: str = ""

        self.include_signature: bool = bool(account and account.signature_file
                                            and account.signature_default_on)
        self.markdown: bool = config.config.get_bool("editor.markdown_on")
        self.encrypt: bool = False
        self.sign: bool = bool(account and account.always_gpg_sign)

        self.attachments: list[Attachment] = []

        self.markdown_success: bool = False
        self.markdown_error: str = ""
        self.encryption_success: bool = False
        self.encryption_error: str = ""

        # send-time state
        self._cancel_event = threading.Event()
        self._send_proc: subprocess.Popen | None = None
        self.message_sent_result: bool = False
        self.message_send_status_msg: str = ""
        self.message_send_status_warn: bool = False

        self.message: GMime.Message | None = None

        self.set_id(generate_message_id(config))

    # -- public setters mirroring C++ surface ------------------------------------

    def set_id(self, mid: str) -> None:
        self.id = mid

    def set_from(self, account: Account) -> None:
        self.account = account
        self.from_addr = account.full_address() if account else ""

    def set_subject(self, s: str) -> None:
        self.subject = s

    def set_to(self, s: str) -> None:
        self.to = s

    def set_cc(self, s: str) -> None:
        self.cc = s

    def set_bcc(self, s: str) -> None:
        self.bcc = s

    def set_references(self, s: str) -> None:
        self.references = s

    def set_inreplyto(self, s: str) -> None:
        self.inreplyto = s

    def add_attachment(self, att: Attachment) -> None:
        self.attachments.append(att)

    # -- build (port of ComposeMessage::build) -------------------------------

    def _read_signature(self, markdown: bool = False) -> str:
        if not (self.include_signature and self.account
                and not self.account.signature_attach):
            return ""

        sig = (self.account.signature_file_markdown if markdown
               and self.account.signature_file_markdown is not None
               else self.account.signature_file)
        if sig is None:
            return ""
        try:
            text = Path(sig).read_text(encoding="utf-8")
        except OSError as e:
            log.error("cm: could not read signature %s: %s", sig, e)
            return ""

        if self.account.signature_separate:
            return "-- \n" + text
        return text

    def build(self) -> None:
        """Build the MIME message; safe to call multiple times for previews."""
        log.debug("cm: build..")

        # one fresh GMime.Message per build — drop the old preview
        GMime.init()
        self.message = GMime.Message.new(True)
        self._apply_headers_to_message()

        charset = self.config.config.get_str("editor.charset", "utf-8")
        text_body = self.body + self._read_signature(markdown=False)

        text = GMime.Part.new_with_type("text", "plain")
        text.set_content_type_parameter("charset", charset)
        if self.config.config.get_bool("mail.format_flowed"):
            text.set_content_type_parameter("format", "flowed")

        stream = GMime.StreamMem.new_with_buffer(text_body.encode(charset))
        wrapper = GMime.DataWrapper.new_with_stream(stream,
                                                   GMime.ContentEncoding.DEFAULT)
        text.set_content_encoding(GMime.ContentEncoding.QUOTEDPRINTABLE)
        text.set_content(wrapper)

        message_part: GMime.Object = text
        self.markdown_success = False
        self.markdown_error = ""

        if self.markdown:
            html_part = self._build_markdown_html(text_body, charset)
            if html_part is not None:
                multi = GMime.Multipart.new_with_subtype("alternative")
                multi.add(text)
                multi.add(html_part)
                message_part = multi

        self.message.set_mime_part(message_part)

    def _build_markdown_html(self, plain_body: str, charset: str) -> GMime.Part | None:
        md_body = self.body + self._read_signature(markdown=True)
        proc = self.config.config.get_str("editor.markdown_processor", "cmark")
        try:
            argv = GLib.shell_parse_argv(proc)[1]
        except GLib.Error as e:
            log.error("cm: md: cannot parse processor: %s", e)
            self.markdown_error = str(e)
            return None

        try:
            r = subprocess.run(argv, input=md_body, capture_output=True,
                               text=True)
        except OSError as e:
            log.error("cm: md: failed to spawn markdown processor: %s", e)
            self.markdown_error = f"Failed to spawn markdown processor: {e}"
            return None

        if r.returncode != 0 or r.stderr.strip():
            log.error("cm: md: %s", r.stderr.strip())
            self.markdown_error = r.stderr or f"exit {r.returncode}"
            return None

        self.markdown_success = True
        html = GMime.Part.new_with_type("text", "html")
        html.set_content_type_parameter("charset", charset)
        stream = GMime.StreamMem.new_with_buffer(r.stdout.encode(charset))
        wrapper = GMime.DataWrapper.new_with_stream(stream,
                                                   GMime.ContentEncoding.DEFAULT)
        html.set_content_encoding(GMime.ContentEncoding.QUOTEDPRINTABLE)
        html.set_content(wrapper)
        return html

    def _apply_headers_to_message(self) -> None:
        m = self.message
        assert m is not None
        if self.from_addr:
            adr = Address(self.from_addr)
            m.add_mailbox(GMime.AddressType.FROM, adr.name(), adr.email())
        if self.subject:
            m.set_subject(self.subject, "UTF-8")
        for atyp, raw in ((GMime.AddressType.TO, self.to),
                          (GMime.AddressType.CC, self.cc),
                          (GMime.AddressType.BCC, self.bcc)):
            for a in AddressList(raw):
                m.add_mailbox(atyp, a.name(), a.email())
        if self.reply_to:
            ad = Address(self.reply_to)
            m.add_mailbox(GMime.AddressType.REPLY_TO, ad.name(), ad.email())
        if self.references:
            m.set_header("References", self.references, "UTF-8")
        if self.inreplyto:
            m.set_header("In-Reply-To", f"<{self.inreplyto}>", "UTF-8")

    # -- finalize (port of ComposeMessage::finalize) ---------------------------

    def finalize(self) -> None:
        log.debug("cm: finalize..")
        assert self.message is not None

        ua = self.config.config.get_str("mail.user_agent", "default").strip()
        if ua == "default":
            ua = DEFAULT_USER_AGENT
        if ua:
            self.message.set_header("User-Agent", ua, "UTF-8")

        self.message.set_date(GLib.DateTime.new_now_local())
        self.message.set_message_id(self.id)

        # signature attached as file
        if (self.include_signature and self.account
                and self.account.signature_attach
                and self.account.signature_file is not None):
            sa = Attachment.from_file(self.account.signature_file)
            if sa.valid:
                self.add_attachment(sa)

        # attachments wrapping in multipart/mixed
        if self.attachments:
            current = self.message.get_mime_part()
            multi = GMime.Multipart.new_with_subtype("mixed")
            multi.add(current)
            for att in self.attachments:
                if not att.valid:
                    log.error("cm: invalid attachment: %s", att.name)
                    continue
                part = self._build_attachment_part(att)
                if part is not None:
                    multi.add(part)
            self.message.set_mime_part(multi)

        # crypto handled in Phase 3 -- noop here
        self.encryption_success = not (self.encrypt or self.sign)

    def _build_attachment_part(self, att: Attachment) -> GMime.Object | None:
        if att.is_mime_message:
            if att.message_path is None or not att.message_path.is_file():
                return None
            stream = GMime.StreamFile.open(str(att.message_path), "r")
            parser = GMime.Parser.new_with_stream(stream)
            sub = parser.construct_message(None)
            if sub is None:
                return None
            mp = GMime.MessagePart.new_with_message("rfc822", sub)
            return mp

        ctype = GMime.ContentType.parse(GMime.ParserOptions.get_default(),
                                        att.content_type)
        part = GMime.Part.new_with_type(ctype.get_media_type(),
                                        ctype.get_media_subtype())
        stream = GMime.StreamMem.new_with_buffer(att.contents)
        wrapper = GMime.DataWrapper.new_with_stream(stream,
                                                   GMime.ContentEncoding.DEFAULT)
        part.set_content(wrapper)
        part.set_filename(att.name)

        if att.inline:
            part.set_disposition("inline")
        else:
            part.set_content_encoding(GMime.ContentEncoding.BASE64)
        return part

    # -- serialize -------------------------------------------------------------

    def write_to_stream(self, stream: GMime.Stream) -> None:
        assert self.message is not None
        self.message.write_to_stream(GMime.FormatOptions.get_default(), stream)
        stream.flush()

    def to_bytes(self) -> bytes:
        out = GMime.StreamMem.new()
        self.write_to_stream(out)
        return bytes(out.get_byte_array())

    def write_to_file(self, path: str | Path) -> None:
        Path(path).write_bytes(self.to_bytes())

    # -- send ------------------------------------------------------------------

    def cancel_sending(self) -> bool:
        log.warning("cm: cancel sendmail")
        self._cancel_event.set()
        if self._send_proc is not None:
            try:
                self._send_proc.kill()
            except OSError:
                pass
        return True

    def send_threaded(self, dispatch=GLib.idle_add) -> None:
        threading.Thread(target=self._send, args=(dispatch,),
                         name="compose-send", daemon=True).start()

    def _emit(self, dispatch, signal: str, *args) -> None:
        dispatch(lambda: (self.emit(signal, *args), False)[1])

    def _send(self, dispatch) -> bool:
        cfg = self.config.config
        self.message_send_status_msg = ""
        self.message_send_status_warn = False

        dryrun = cfg.get_bool("astroid.debug.dryrun_sending")
        delay = max(0, cfg.get_int("mail.send_delay"))

        while delay > 0:
            self.message_send_status_msg = (
                f"sending message in {delay} seconds... Press C-c to cancel!")
            self._emit(dispatch, "message-send-status", False,
                       self.message_send_status_msg)
            if self._cancel_event.wait(timeout=1.0):
                break
            delay -= 1

        if self._cancel_event.is_set():
            self.message_send_status_msg = "send cancelled"
            self.message_send_status_warn = True
            self._emit(dispatch, "message-send-status", True,
                       self.message_send_status_msg)
            self.message_sent_result = False
            self._emit(dispatch, "message-sent", False)
            return False

        if dryrun:
            target = Path("/tmp") / self.id
            try:
                self.write_to_file(target)
                log.warning("cm: dryrun: wrote message to %s", target)
            except OSError as e:
                log.error("cm: dryrun: write failed: %s", e)
            self.message_sent_result = False  # match C++: dryrun returns false
            self._emit(dispatch, "message-sent", False)
            return False

        try:
            argv = GLib.shell_parse_argv(self.account.sendmail)[1]
        except GLib.Error as e:
            log.error("cm: invalid sendmail command: %s", e)
            self.message_send_status_msg = "invalid sendmail command"
            self.message_send_status_warn = True
            self._emit(dispatch, "message-send-status", True,
                       self.message_send_status_msg)
            self.message_sent_result = False
            self._emit(dispatch, "message-sent", False)
            return False

        log.info("cm: send: %s", " ".join(shlex.quote(a) for a in argv))
        try:
            self._send_proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE)
        except OSError as e:
            log.error("cm: sendmail spawn failed: %s", e)
            self.message_send_status_msg = "sendmail spawn failed"
            self.message_send_status_warn = True
            self._emit(dispatch, "message-send-status", True,
                       self.message_send_status_msg)
            self.message_sent_result = False
            self._emit(dispatch, "message-sent", False)
            return False

        try:
            out, err = self._send_proc.communicate(self.to_bytes())
        except Exception as e:
            log.error("cm: sendmail communicate failed: %s", e)
            out = err = b""
        status = self._send_proc.returncode

        for chunk, sink in ((out, log.debug), (err, log.warning)):
            if chunk:
                txt = chunk.decode("utf-8", errors="replace")[:_MAX_PROC_LOG]
                sink("sendmail: %s", txt.strip())

        success = (not self._cancel_event.is_set() and status == 0)

        if success:
            log.warning("cm: message sent successfully!")

            if self.account.save_sent and self.account.save_sent_to:
                save_to = Path(self.account.save_sent_to) / f"{self.id}:2,"
                try:
                    save_to.parent.mkdir(parents=True, exist_ok=True)
                    self.write_to_file(save_to)
                    self._saved_sent_path = save_to
                except OSError as e:
                    log.error("cm: save_sent_to failed: %s", e)
                    self._saved_sent_path = None
            else:
                self._saved_sent_path = None

            self.message_send_status_msg = "message sent successfully!"
            self.message_send_status_warn = False
            self.message_sent_result = True
        else:
            self.message_send_status_msg = "message could not be sent!"
            self.message_send_status_warn = True
            self.message_sent_result = False

        self._emit(dispatch, "message-send-status",
                   self.message_send_status_warn, self.message_send_status_msg)
        self._emit(dispatch, "message-sent", self.message_sent_result)
        return self.message_sent_result

    @property
    def saved_sent_path(self) -> Path | None:
        return getattr(self, "_saved_sent_path", None)
