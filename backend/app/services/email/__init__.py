from app.config import Settings
from app.services.email.base import (
    Attachment,
    EmailError,
    EmailPermanentError,
    EmailSender,
    EmailTransientError,
    OutgoingEmail,
    SendResult,
)
from app.services.email.console import ConsoleEmailSender
from app.services.email.gmail_api import GmailApiSender

__all__ = [
    "Attachment",
    "ConsoleEmailSender",
    "EmailError",
    "EmailPermanentError",
    "EmailSender",
    "EmailTransientError",
    "GmailApiSender",
    "OutgoingEmail",
    "SendResult",
    "build_email_sender",
]


def build_email_sender(settings: Settings) -> EmailSender:
    if settings.email_backend == "gmail_api":
        if not (
            settings.gmail_client_id
            and settings.gmail_client_secret
            and settings.gmail_refresh_token
            and settings.gmail_sender
        ):
            raise ValueError(
                "EMAIL_BACKEND=gmail_api needs GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, "
                "GMAIL_REFRESH_TOKEN and GMAIL_SENDER"
            )
        return GmailApiSender(
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret.get_secret_value(),
            refresh_token=settings.gmail_refresh_token.get_secret_value(),
            sender_address=settings.gmail_sender,
        )
    return ConsoleEmailSender()
