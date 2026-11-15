import smtplib
from datetime import timedelta

import pytest

from app.clock import FakeClock
from app.config import Settings
from app.mailer import (
    CaptureMailer, DisabledMailer, MailSendError, NotConfigured, SmtpMailer, build_mailer, compose_message,
)


def test_message_is_vietnamese_then_english_with_code_and_lifetime():
    body = compose_message("from@x.test", "to@x.test", "123456", 10).get_content()
    assert "Mã đăng nhập" in body and "Your sign-in code" in body
    assert body.index("Mã đăng nhập") < body.index("Your sign-in code")
    assert body.count("123456") == 2 and "10 phút" in body and "10 minutes" in body


async def test_capture_mailer_writes_one_file_per_message(tmp_path):
    mailer = CaptureMailer(tmp_path, FakeClock())
    await mailer.send_code("a@x.test", "111111", 10)
    await mailer.send_code("b@x.test", "222222", 10)
    assert [m["code"] for m in mailer.messages("a@x.test")] == ["111111"]
    assert len(list(tmp_path.glob("*.json"))) == 2


async def test_old_capture_files_are_deleted_on_write_and_at_startup(tmp_path):
    clock = FakeClock()
    mailer = CaptureMailer(tmp_path, clock, ttl_minutes=60)
    await mailer.send_code("old@x.test", "111111", 10)
    clock.advance(minutes=61)
    await mailer.send_code("new@x.test", "222222", 10)
    assert mailer.messages("old@x.test") == [] and len(mailer.messages("new@x.test")) == 1
    clock.advance(minutes=61)
    CaptureMailer(tmp_path, clock, ttl_minutes=60)  # start-up purge
    assert list(tmp_path.glob("*.json")) == []


async def test_disabled_mailer_raises_and_writes_nothing(tmp_path):
    with pytest.raises(NotConfigured):
        await DisabledMailer().send_code("a@x.test", "111111", 10)
    assert list(tmp_path.iterdir()) == []


def smtp_settings(**kw) -> Settings:
    return Settings(smtp_host="smtp.test", smtp_port=587, smtp_user="u", smtp_password="p",
                    smtp_from="no-reply@x.test", mail_backend="smtp", **kw)


async def test_smtp_mailer_uses_starttls_login_and_sends(monkeypatch):
    calls = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            calls["connect"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            calls["starttls"] = True

        def login(self, user, password):
            calls["login"] = (user, password)

        def send_message(self, message):
            calls["message"] = message

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    await SmtpMailer(smtp_settings()).send_code("to@x.test", "654321", 10)
    assert calls["connect"] == ("smtp.test", 587) and calls["starttls"] and calls["login"] == ("u", "p")
    message = calls["message"]
    assert message["To"] == "to@x.test" and message["From"] == "no-reply@x.test"
    assert "654321" in message.get_content()


async def test_smtp_failure_becomes_mail_send_error(monkeypatch):
    def boom(*args, **kwargs):
        raise ConnectionRefusedError("no route")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    with pytest.raises(MailSendError):
        await SmtpMailer(smtp_settings()).send_code("to@x.test", "654321", 10)


def test_build_mailer_picks_the_backend(tmp_path):
    clock = FakeClock()
    assert isinstance(build_mailer(Settings(mail_backend="smtp", smtp_host="h", smtp_from="f"), clock), SmtpMailer)
    assert isinstance(build_mailer(Settings(mail_backend="capture", mail_capture_dir=str(tmp_path)), clock), CaptureMailer)
    assert isinstance(build_mailer(Settings(mail_backend="disabled"), clock), DisabledMailer)
