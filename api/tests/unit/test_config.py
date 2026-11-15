import dataclasses

import pytest

from app.config import ConfigError, Settings, load_settings, parse_dotenv, validate


def make(**kw) -> Settings:
    return dataclasses.replace(Settings(), pepper=b"p" * 32, postgres_password="x", **kw)


def test_capture_is_refused_in_production():
    with pytest.raises(ConfigError, match="capture"):
        validate(make(app_env="production", mail_backend="capture"))


def test_smtp_without_settings_is_refused():
    with pytest.raises(ConfigError, match="SMTP"):
        validate(make(mail_backend="smtp"))


def test_disabled_mail_without_google_is_refused_in_production():
    with pytest.raises(ConfigError, match="nobody could sign in"):
        validate(make(app_env="production", mail_backend="disabled"))


@pytest.mark.parametrize(
    "kw",
    [
        dict(app_env="development", mail_backend="capture"),
        dict(app_env="development", mail_backend="disabled"),
        dict(app_env="production", mail_backend="disabled", google_client_id="id", google_client_secret="secret"),
        dict(app_env="production", mail_backend="smtp", smtp_host="smtp.test", smtp_from="a@b.test"),
    ],
)
def test_allowed_combinations(kw):
    assert validate(make(**kw))


def test_unknown_values_are_refused():
    with pytest.raises(ConfigError):
        validate(make(app_env="staging"))
    with pytest.raises(ConfigError):
        validate(make(mail_backend="pigeon"))
    with pytest.raises(ConfigError):
        validate(make(code_rate_per_email=0))


def test_defaults_and_empty_external_values_mean_not_configured(tmp_path):
    (tmp_path / "api_rw_password").write_text("pw\n")
    (tmp_path / "auth_pepper").write_text("pepper-pepper-pepper\n")
    (tmp_path / "api_external").write_text("SMTP_HOST=\nSMTP_PORT=\nGOOGLE_CLIENT_ID=\nGOOGLE_CLIENT_SECRET=\n")
    s = load_settings(env={}, secrets_dir=tmp_path)
    assert (s.app_env, s.mail_backend, s.session_days) == ("development", "capture", 7)
    assert (s.code_rate_per_email, s.code_rate_per_ip, s.verify_rate_per_ip) == (5, 20, 30)
    assert s.smtp_configured is False and s.google_configured is False
    assert s.postgres_password == "pw" and s.pepper == b"pepper-pepper-pepper"
    assert s.session_seconds == 604800


def test_environment_overrides_and_bad_numbers(tmp_path):
    (tmp_path / "api_rw_password").write_text("pw")
    (tmp_path / "auth_pepper").write_text("x" * 20)
    s = load_settings(env={"CODE_RATE_PER_EMAIL": "9", "SESSION_DAYS": "3", "PUBLIC_ORIGIN": "https://a.test/"}, secrets_dir=tmp_path)
    assert (s.code_rate_per_email, s.session_days, s.public_origin) == (9, 3, "https://a.test")
    with pytest.raises(ConfigError, match="whole number"):
        load_settings(env={"SESSION_DAYS": "seven"}, secrets_dir=tmp_path)


def test_parse_dotenv_handles_comments_blank_lines_and_quotes():
    text = "# comment\n\nA=1\nB = two \nC=\"three\"\nD='four'\nbadline\nE=\n"
    assert parse_dotenv(text) == {"A": "1", "B": "two", "C": "three", "D": "four", "E": ""}
