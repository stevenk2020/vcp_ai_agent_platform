import logging

from sync.scrub import ScrubFilter, Scrubber

SECRETS = ["db.example.net", "reader", "Sup3r-Secret-PW", "erp_prod", "pg-pass-123"]


def test_every_secret_is_removed_everywhere():
    s = Scrubber(SECRETS)
    text = ("Adaptive Server connection failed (DB.EXAMPLE.NET) user=reader pw=Sup3r-Secret-PW "
            "Server=db.example.net;Database=erp_prod;Uid=reader;Pwd=Sup3r-Secret-PW; postgres://x:pg-pass-123@h")
    out = s.clean(text)
    for secret in SECRETS:
        assert secret.lower() not in out.lower()
    assert "***" in out


def test_traceback_style_text_is_cleaned():
    out = Scrubber(SECRETS).clean('File "x.py", line 3\n  connect(password="Sup3r-Secret-PW")\nOperationalError')
    assert "Sup3r-Secret-PW" not in out and "OperationalError" in out


def test_length_is_limited_and_control_characters_removed():
    out = Scrubber([]).clean("a" * 5000)
    assert len(out) == 500
    assert "\x00" not in Scrubber([]).clean("a\x00b\x07c") and Scrubber([]).clean("a\x00b") == "a b"


def test_empty_secret_list_changes_nothing_else():
    assert Scrubber([]).clean("plain text") == "plain text"
    assert Scrubber([""]).clean("plain text") == "plain text"


def test_overlapping_secrets_are_all_removed():
    out = Scrubber(["pass", "password123"]).clean("my password123 and pass")
    assert "pass" not in out.replace("***", "")


def test_logging_filter_cleans_messages_args_and_exceptions(caplog):
    logger = logging.getLogger("scrub-test")
    logger.addFilter(ScrubFilter(Scrubber(SECRETS)))
    caplog.set_level(logging.DEBUG, logger="scrub-test")
    try:
        raise RuntimeError("login for reader failed with Sup3r-Secret-PW")
    except RuntimeError:
        logger.error("failed for %s", "reader", exc_info=True)
    caplog.handler.setFormatter(logging.Formatter("%(message)s"))
    text = caplog.text + " ".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    assert "Sup3r-Secret-PW" not in text and "reader" not in text
