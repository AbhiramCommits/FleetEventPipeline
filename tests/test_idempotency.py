"""Idempotency: rebuilding the mart twice yields identical row counts."""

from __future__ import annotations

import pytest

from src.elt.run import run_elt


def test_elt_is_idempotent(conn) -> None:
    """Running the ELT twice produces identical mart row counts."""
    staged = conn.execute("SELECT count(*) FROM stg.gps_ping").fetchone()[0]
    if staged == 0:
        pytest.skip("staging is empty; run `make demo` first")

    first = run_elt()["counts"]
    second = run_elt()["counts"]
    assert first == second, "mart counts changed between identical ELT runs"
    assert first["mart.fact_gps_ping"] > 0


def test_fact_counts_match_staging(conn) -> None:
    """fact_gps_ping has one row per clean staged ping (no loss, no duplication)."""
    staged = conn.execute(
        "SELECT count(*) FROM stg.gps_ping WHERE lat IS NOT NULL AND lon IS NOT NULL"
    ).fetchone()[0]
    fact = conn.execute("SELECT count(*) FROM mart.fact_gps_ping").fetchone()[0]
    assert fact == staged
