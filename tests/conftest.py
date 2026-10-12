"""Shared pytest configuration: disable vendor failure injection by default."""

from __future__ import annotations

import os

os.environ.setdefault("MOCKAPI_FAILURE_RATE", "0")
