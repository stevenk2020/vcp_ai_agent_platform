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


@pytest.mark.skipif("access_requested" not in __import__("app.audit", fromlist=["ACTIONS"]).ACTIONS,
                    reason="the access_requested action arrives with the access request endpoint (T019)")
async def test_access_requested_detail_never_carries_text():
    # only flags are allowed in the detail; the request's own text must never be written to the audit trail
    for key in ("name", "reason_text"):
        with pytest.raises(ValueError, match="must not contain"):
            await record(None, FakeClock(), "access_requested", detail={key: "typed text"})


async def test_access_text_keys_are_forbidden_in_every_audit_detail():
    for key in ("name", "reason_text"):
        with pytest.raises(ValueError, match="must not contain"):
            await record(None, FakeClock(), "code_requested", detail={key: "x"})


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
