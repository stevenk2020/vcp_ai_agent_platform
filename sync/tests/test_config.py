import pytest

from sync import config as cfg


def secrets_dir(tmp_path, source="", pg="pgpw"):
    (tmp_path / "sync_writer_password").write_text(pg)
    (tmp_path / "source_sqlserver").write_text(source)
    return tmp_path


FULL = "SQL_SERVER_HOST=db.example\nSQL_SERVER_PORT=1433\nSQL_SERVER_USER=reader\nSQL_SERVER_PASSWORD=Pw-1234\nSQL_SERVER_DATABASE=erp\n"


def test_defaults(tmp_path):
    s = cfg.load(env={}, secrets_dir=secrets_dir(tmp_path, FULL))
    assert (s.sync_interval_minutes, s.sync_poll_seconds, s.source_snapshot_read) == (10, 5, "off")
    assert (s.source_read_timeout_seconds, s.source_encrypt, s.sync_retry_attempts) == (300, "require", 3)
    assert (s.sync_retry_base_seconds, s.guard_min_fraction, s.guard_min_rows) == (15, 0.5, 1000)
    assert (s.swap_lock_timeout_seconds, s.sync_stale_minutes, s.sync_failing_after) == (15, 30, 3)
    assert (s.sync_log_retention_days, s.sync_request_ttl_minutes) == (30, 15)
    assert s.source_configured and s.source_port == 1433


@pytest.mark.parametrize("env", [
    {"SOURCE_SNAPSHOT_READ": "maybe"},
    {"SOURCE_ENCRYPT": "sometimes"},
    {"SOURCE_READ_TIMEOUT_SECONDS": "179"},
    {"GUARD_MIN_FRACTION": "0"},
    {"GUARD_MIN_FRACTION": "1.5"},
    {"SYNC_INTERVAL_MINUTES": "0"},
    {"SYNC_FAILING_AFTER": "0"},
    {"SYNC_REQUEST_TTL_MINUTES": "0"},
    {"SYNC_POLL_SECONDS": "abc"},
])
def test_each_invalid_value_is_refused(tmp_path, env):
    with pytest.raises(cfg.ConfigError):
        cfg.load(env=env, secrets_dir=secrets_dir(tmp_path, FULL))


def test_a_read_limit_of_180_is_accepted(tmp_path):
    assert cfg.load(env={"SOURCE_READ_TIMEOUT_SECONDS": "180"}, secrets_dir=secrets_dir(tmp_path, FULL)).source_read_timeout_seconds == 180


def test_empty_or_partial_source_secret_means_not_configured_not_an_error(tmp_path):
    assert cfg.load(env={}, secrets_dir=secrets_dir(tmp_path, "")).source_configured is False
    partial = "SQL_SERVER_HOST=db.example\nSQL_SERVER_USER=reader\n"
    assert cfg.load(env={}, secrets_dir=secrets_dir(tmp_path, partial)).source_configured is False
    assert cfg.load(env={}, secrets_dir=tmp_path / "missing").source_configured is False


def test_repr_hides_every_secret(tmp_path):
    s = cfg.load(env={}, secrets_dir=secrets_dir(tmp_path, FULL, pg="pg-secret-value"))
    text = repr(s)
    for secret in ("pg-secret-value", "Pw-1234", "db.example", "reader", "erp"):
        assert secret not in text
    assert set(s.secret_values) == {"pg-secret-value", "Pw-1234", "db.example", "reader", "erp"}


def test_bad_source_port_is_refused(tmp_path):
    with pytest.raises(cfg.ConfigError):
        cfg.load(env={}, secrets_dir=secrets_dir(tmp_path, FULL.replace("1433", "x")))
