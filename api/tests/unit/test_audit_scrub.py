import json
import logging

import pytest

from app.audit import JsonFormatter, SecretScrubFilter, record
from app.clock import FakeClock


async def test_record_rejects_secret_keys_before_touching_the_database():
    for key in ("code", "token", "password", "Cookie", "authorization"):
        with pytest.raises(ValueError, match="must not contain"):
            await record(None, FakeClock(), "code_requested", detail={key: "x"})
    with pytest.raises(ValueError):
        await record(None, FakeClock(), "code_requested", detail={"nested": {"secret": "x"}})


async def test_record_rejects_unknown_actions():
    with pytest.raises(ValueError, match="unknown audit action"):
        await record(None, FakeClock(), "made_up")


def test_log_filter_drops_secret_fields_and_the_formatter_never_prints_them():
    logger = logging.LogRecord("t", logging.INFO, __file__, 1, "hello", (), None)
    logger.code = "123456"
    logger.token = "abcdef"
    logger.ip = "10.0.0.1"
    assert SecretScrubFilter().filter(logger) is True
    assert not hasattr(logger, "code") and not hasattr(logger, "token")
    payload = json.loads(JsonFormatter().format(logger))
    assert payload["ip"] == "10.0.0.1" and payload["message"] == "hello"
    assert "123456" not in json.dumps(payload) and "abcdef" not in json.dumps(payload)
