"""Keep secrets out of logs, error texts and responses (research R-13)."""

from __future__ import annotations

import logging
import re

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class Scrubber:
    """Replaces every secret value with `***`, drops control characters and limits the length."""

    def __init__(self, secrets: list[str]) -> None:
        # longest first so a secret that contains another one is removed whole
        unique = sorted({s for s in secrets if s}, key=len, reverse=True)
        self._patterns = [re.compile(re.escape(s), re.IGNORECASE) for s in unique]

    def clean(self, text: object, limit: int = 500) -> str:
        value = str(text)
        for pattern in self._patterns:
            value = pattern.sub("***", value)
        value = _CONTROL.sub(" ", value)
        return value[:limit]


class ScrubFilter(logging.Filter):
    """Logging filter: rewrites the message of every record through the scrubber."""

    def __init__(self, scrubber: Scrubber) -> None:
        super().__init__()
        self.scrubber = scrubber

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # a broken format string must not crash the worker
            message = str(record.msg)
        record.msg = self.scrubber.clean(message, limit=100_000)
        record.args = None
        if record.exc_info:
            record.exc_text = self.scrubber.clean(logging.Formatter().formatException(record.exc_info), limit=100_000)
            record.exc_info = None
        return True
