"""Sending sign-in codes. Backends: smtp, capture (development and tests only) and disabled."""

from __future__ import annotations

import asyncio
import json
import smtplib
import time
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from app.clock import Clock
from app.config import Settings

SUBJECT = "Mã đăng nhập / Sign-in code"


class NotConfigured(Exception):
    """Email codes are switched off."""


class MailSendError(Exception):
    """The message could not be delivered."""


class Mailer(Protocol):
    async def send_code(self, to: str, code: str, minutes: int) -> None: ...


def compose_message(sender: str, to: str, code: str, minutes: int) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = SUBJECT
    message.set_content(
        f"Mã đăng nhập của bạn là: {code}\n"
        f"Mã có hiệu lực trong {minutes} phút. Nếu bạn không yêu cầu mã này, hãy bỏ qua email này.\n"
        "\n---\n\n"
        f"Your sign-in code is: {code}\n"
        f"It is valid for {minutes} minutes. If you did not request it, ignore this email.\n"
    )
    return message


class CaptureMailer:
    """Development and test sink: one JSON file per message. Files older than the TTL are deleted at
    start-up and on every write, so readable codes never accumulate."""

    def __init__(self, directory: str | Path, clock: Clock, ttl_minutes: int = 60) -> None:
        self.directory = Path(directory)
        self.clock = clock
        self.ttl = timedelta(minutes=ttl_minutes)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.purge()

    def purge(self) -> None:
        now = self.clock.now()
        for path in self.directory.glob("*.json"):
            try:
                created = datetime.fromisoformat(json.loads(path.read_text(encoding="utf-8"))["created_at"])
            except (OSError, ValueError, KeyError):
                continue
            if now - created > self.ttl:
                path.unlink(missing_ok=True)

    async def send_code(self, to: str, code: str, minutes: int) -> None:
        self.purge()
        message = compose_message("capture@localhost", to, code, minutes)
        payload = {
            "to": to,
            "code": code,
            "created_at": self.clock.now().isoformat(),
            "subject": message["Subject"],
            "body": message.get_content(),
        }
        # file names sort by real arrival time (not the injected clock, which tests may freeze)
        name = f"{time.time_ns():020d}-{uuid.uuid4().hex[:8]}.json"
        (self.directory / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def messages(self, to: str | None = None) -> list[dict]:
        """Captured messages, oldest first (a helper for tests and for development)."""
        items = []
        for path in sorted(self.directory.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            if to is None or data["to"] == to:
                items.append(data)
        return items


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _send(self, message: EmailMessage) -> None:
        s = self.settings
        try:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
                smtp.starttls()
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailSendError("mail could not be sent") from exc

    async def send_code(self, to: str, code: str, minutes: int) -> None:
        message = compose_message(self.settings.smtp_from, to, code, minutes)
        await asyncio.to_thread(self._send, message)


class DisabledMailer:
    async def send_code(self, to: str, code: str, minutes: int) -> None:
        raise NotConfigured("email codes are switched off")


def build_mailer(settings: Settings, clock: Clock) -> Mailer:
    if settings.mail_backend == "smtp":
        return SmtpMailer(settings)
    if settings.mail_backend == "capture":
        return CaptureMailer(settings.mail_capture_dir, clock, settings.mail_capture_ttl_minutes)
    return DisabledMailer()
