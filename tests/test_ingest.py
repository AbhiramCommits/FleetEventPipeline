"""Tests for the ingestion job: incremental watermarks, retries and envelopes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from src.common.db import get_conn
from src.ingest import job
from src.ingest.client import FleetApiClient
from src.ingest.eventgrid import _chunks, build_envelopes


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _ping(i: int) -> dict[str, Any]:
    base = datetime(2026, 1, 1, tzinfo=UTC)
    from datetime import timedelta

    return {
        "id": f"test-{i:06d}",
        "vehicleId": f"VEH-{(i % 3) + 1:03d}",
        "operatorId": "OP-001",
        "ts": _iso(base + timedelta(minutes=i)),
        "lat": 39.1,
        "lon": -94.5,
        "speedMph": 10.0,
        "heading": 90.0,
        "engineOn": True,
        "odometerMi": 1000.0,
        "engineHours": 10.0,
        "fuelLevelPct": 80.0,
    }


def _vendor_transport(store: dict[str, Any]) -> httpx.MockTransport:
    """A minimal vendor implementing the same page contract as the mock API."""

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.headers.get("authorization", "").startswith("Bearer "):
            return httpx.Response(401, json={"detail": "no auth"})
        params = request.url.params
        after = params.get("after")
        cursor = int(params.get("cursor", "0") or 0)
        limit = int(params.get("limit", "1000"))
        rows = [e for e in store["events"] if after is None or e["ts"] > after]
        page = rows[cursor : cursor + limit]
        has_next = cursor + limit < len(rows)
        return httpx.Response(
            200,
            json={
                "data": page,
                "pagination": {"end_cursor": str(cursor + limit) if has_next else None, "has_next": has_next},
            },
        )

    return httpx.MockTransport(handler)


@pytest.fixture()
def db_conn():
    """Yield an autocommit connection or skip when Postgres is unavailable."""
    try:
        conn = get_conn(autocommit=True)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres unavailable: {exc}")
    yield conn
    conn.close()


def test_second_run_reads_zero_and_delta_only(db_conn) -> None:
    """(a) no new data -> 0 rows; (b) new data -> only the delta is read."""
    source = "gps_test"
    job.SOURCE_TABLE[source] = ("raw.gps_ping_raw", "/v1/fleet/gps")
    store: dict[str, Any] = {"events": [_ping(i) for i in range(25)]}
    run_id = uuid.uuid4()
    client = FleetApiClient(base_url="http://vendor", transport=_vendor_transport(store))
    try:
        # Clean any prior state for this isolated source.
        db_conn.execute("DELETE FROM ctl.watermark WHERE source_name = %s", (source,))
        db_conn.execute("DELETE FROM raw.gps_ping_raw WHERE run_id = %s", (run_id,))

        first = job.run_source(source, client, db_conn, run_id, limit=10, publish_events=False)
        assert first["rows_read"] == 25
        assert first["rows_landed"] == 25

        second = job.run_source(source, client, db_conn, run_id, limit=10, publish_events=False)
        assert second["rows_read"] == 0, "second run should see no new data"

        store["events"].extend(_ping(i) for i in range(25, 30))
        third = job.run_source(source, client, db_conn, run_id, limit=10, publish_events=False)
        assert third["rows_read"] == 5, "only the 5 new pings should be read"
    finally:
        db_conn.execute("DELETE FROM raw.gps_ping_raw WHERE run_id = %s", (run_id,))
        db_conn.execute("DELETE FROM ctl.watermark WHERE source_name = %s", (source,))


def test_retries_succeed_through_injected_429_and_503() -> None:
    """(c) transient 429/503 responses are retried and the page eventually lands."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        if calls["n"] == 2:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={"data": [_ping(0)], "pagination": {"end_cursor": None, "has_next": False}},
        )

    client = FleetApiClient(base_url="http://vendor", transport=httpx.MockTransport(handler), max_attempts=5)
    pages = list(client.paginate("/v1/fleet/gps"))
    assert len(pages) == 1
    assert len(pages[0]["data"]) == 1
    assert client.stats.retries >= 2, "two transient failures should be retried"


def test_eventgrid_envelopes_match_schema() -> None:
    """(d) envelopes carry a valid Event Grid schema shape."""
    run_id = str(uuid.uuid4())
    envelopes = build_envelopes("gps", [_ping(1), _ping(2), _ping(4)], run_id)
    assert envelopes, "expected at least one vehicle group"
    for env in envelopes:
        assert set(env) == {"id", "eventType", "subject", "eventTime", "dataVersion", "data"}
        uuid.UUID(env["id"])  # id must be a uuid
        assert env["eventType"] == "Fleet.GpsPingBatchLanded"
        assert env["dataVersion"] == "1.0"
        assert env["subject"].startswith("/fleet/vehicles/")
        datetime.fromisoformat(env["eventTime"].replace("Z", "+00:00"))
        assert env["data"]["source"] == "gps"
        assert env["data"]["count"] == len(env["data"]["rows"])

    geo = build_envelopes("geofence", [{"id": "g1", "vehicleId": "V1", "ts": _iso(datetime.now(UTC))}], run_id)
    assert geo[0]["eventType"] == "Fleet.GeofenceEventLanded"


def test_publish_batches_are_capped_at_100() -> None:
    """Envelopes are posted in arrays of at most 100."""
    batches = list(_chunks([{"id": i} for i in range(250)], 100))
    assert [len(b) for b in batches] == [100, 100, 50]
