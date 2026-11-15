"""Fixtures shared by the unit and integration tests.

The suite must run through `make test-api` (an isolated Compose project): it refuses to start
otherwise, because the integration tests truncate the auth tables.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from app.clock import FakeClock
from app.config import Settings, load_settings

ORIGIN = "https://test.example"


@pytest.fixture(scope="session", autouse=True)
def _require_isolated_project():
    if os.environ.get("API_TEST_ISOLATED") != "1":
        pytest.exit(
            "refusing to run: use `make test-api` (isolated project); these tests truncate tables",
            returncode=2,
        )


@pytest.fixture(scope="session")
def base_settings() -> Settings:
    loaded = load_settings(env={**os.environ, "APP_ENV": "development", "MAIL_BACKEND": "capture"})
    # never use real Google credentials from secrets/api_external in tests
    return dataclasses.replace(
        loaded, public_origin=ORIGIN, google_client_id="test-client-id", google_client_secret="test-client-secret"
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()
