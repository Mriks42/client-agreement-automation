"""Email sending contract. Providers implement `EmailSender`; nothing else imports a provider."""

from dataclasses import dataclass, field
from email.headerregistry import Address
from email.message import EmailMessage as MimeMessage
from email.utils import formatdate, make_msgid
from typing import Protocol


class EmailError(Exception):
    """Base class for send failures."""


class EmailTransientError(EmailError):
    """Worth retrying: rate limits, provider 5xx, network failures."""


class EmailPermanentError(EmailError):
    """Retrying won't help: bad recipient, message rejected, credentials revoked."""


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: bytes
    mime_type: str = "application/pdf"


@dataclass
class OutgoingEmail:
    to: list[str]
    subject: str
    text: str
    html: str | None = None
    from_name: str | None = None
    reply_to: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    # Correlation headers (e.g. X-Agreement-Ref) so bounces can be traced back to a recipient.
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SendResult:
    provider: str
    provider_message_id: str | None
    message_id: str


class EmailSender(Protocol):
    name: str

    async def send(self, email: OutgoingEmail) -> SendResult: ...

    async def check(self) -> None:
        """Raise EmailError if the sender can't currently send (e.g. revoked credentials)."""
        ...


def build_mime(email: OutgoingEmail, sender_address: str) -> tuple[MimeMessage, str]:
    """Render an OutgoingEmail to RFC 5322. Returns the message and its Message-ID."""
    if not email.to:
        raise EmailPermanentError("no recipients")
    msg = MimeMessage()
    username, _, domain = sender_address.partition("@")
    msg["From"] = Address(display_name=email.from_name or "", username=username, domain=domain)
    msg["To"] = ", ".join(email.to)
    msg["Subject"] = email.subject
    msg["Date"] = formatdate(localtime=False, usegmt=True)
    message_id = make_msgid(domain=domain or "localhost")
    msg["Message-ID"] = message_id
    if email.reply_to:
        msg["Reply-To"] = email.reply_to
    for key, value in email.headers.items():
        msg[key] = value
    msg.set_content(email.text)
    if email.html:
        msg.add_alternative(email.html, subtype="html")
    for att in email.attachments:
        maintype, _, subtype = att.mime_type.partition("/")
        msg.add_attachment(att.content, maintype=maintype, subtype=subtype, filename=att.filename)
    return msg, message_id
