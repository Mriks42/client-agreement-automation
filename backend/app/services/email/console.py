"""Dev/test sender: logs the email and keeps it in memory so tests and devs can read links."""

import logging
from collections import deque

from app.services.email.base import OutgoingEmail, SendResult, build_mime

log = logging.getLogger(__name__)


class ConsoleEmailSender:
    name = "console"

    def __init__(self, sender_address: str = "no-reply@localhost", keep: int = 200) -> None:
        self.sender_address = sender_address
        self.outbox: deque[OutgoingEmail] = deque(maxlen=keep)

    async def send(self, email: OutgoingEmail) -> SendResult:
        _, message_id = build_mime(email, self.sender_address)
        self.outbox.append(email)
        log.info(
            "email (console backend)\n  to: %s\n  subject: %s\n%s",
            ", ".join(email.to),
            email.subject,
            email.text,
        )
        return SendResult(provider=self.name, provider_message_id=None, message_id=message_id)

    async def check(self) -> None:
        return None
