"""Shared pytest configuration and fixtures."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("MOCKAPI_FAILURE_RATE", "0")


class Rollback(Exception):
    """Sentinel used to abort a scoped transaction so test mutations are undone."""


@pytest.fixture()
def conn():
    """Yield an autocommit connection, or skip the test when Postgres is down."""
    try:
        from src.common.db import get_conn

        connection = get_conn(autocommit=True)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres unavailable: {exc}")
    yield connection
    connection.close()
