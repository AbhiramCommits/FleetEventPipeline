"""Tests for the mock vendor API: authentication and pagination integrity."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.mockapi import app as mockapi_app
from src.mockapi.app import app, dataset

TOKEN = "local-dev-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _no_injected_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the mock API's transient 429/503 injection for these tests.

    These tests check auth and pagination integrity, not retry behaviour (that is
    the ingest client's job, via tenacity). ``conftest.py`` only *defaults*
    MOCKAPI_FAILURE_RATE to 0, so an environment that sets it (CI uses 0.03 for
    the end-to-end demo) would otherwise make these direct requests fail.
    """
    monkeypatch.setattr(mockapi_app, "FAILURE_RATE", 0.0)


def _walk(client: TestClient, path: str, limit: int) -> list[dict]:
    """Walk every page of a paginated endpoint and return all rows in order."""
    rows: list[dict] = []
    cursor = None
    pages = 0
    while True:
        params: dict[str, object] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        resp = client.get(path, params=params, headers=AUTH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        rows.extend(body["data"])
        pages += 1
        if not body["pagination"]["has_next"]:
            break
        cursor = body["pagination"]["end_cursor"]
        assert pages < 10000, "pagination did not terminate"
    return rows


def test_auth_is_enforced() -> None:
    """Requests without / with a bad bearer token are rejected with 401."""
    client = TestClient(app)
    assert client.get("/v1/fleet/vehicles").status_code == 401
    assert client.get("/v1/fleet/gps", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/v1/fleet/vehicles", headers=AUTH).status_code == 200


def test_gps_pagination_walks_full_set_exactly_once() -> None:
    """Paging yields the full, ordered source stream exactly once (no gaps/overlaps).

    The source deliberately contains injected *duplicate vendor events* (same id),
    so integrity is proven by sequence equality rather than id uniqueness: any
    gap or overlap would shift or drop entries. Two different page sizes must
    produce byte-identical sequences.
    """
    client = TestClient(app)
    expected = [e["id"] for e in dataset()["gps"]]
    rows_1000 = [r["id"] for r in _walk(client, "/v1/fleet/gps", limit=1000)]
    rows_977 = [r["id"] for r in _walk(client, "/v1/fleet/gps", limit=977)]
    assert len(rows_1000) == len(expected)
    assert rows_1000 == expected, "pages are not in the same order as the source"
    assert rows_977 == expected, "page size changed the walked sequence"


def test_geofence_pagination_walks_full_set_exactly_once() -> None:
    """Paging the geofence endpoint covers the full set with no gaps."""
    client = TestClient(app)
    expected = dataset()["geofenceEvents"]
    rows = _walk(client, "/v1/fleet/geofence-events", limit=137)
    assert len(rows) == len(expected)
    assert [r["id"] for r in rows] == [e["id"] for e in expected]


def test_after_filter_returns_only_newer_events() -> None:
    """The ``after`` parameter excludes events at or before the cursor timestamp."""
    client = TestClient(app)
    all_gps = dataset()["gps"]
    midpoint = all_gps[len(all_gps) // 2]["ts"]
    resp = client.get("/v1/fleet/gps", params={"after": midpoint, "limit": 5000}, headers=AUTH)
    assert resp.status_code == 200
    for row in resp.json()["data"]:
        assert row["ts"] > midpoint


def test_reference_endpoints_return_rows() -> None:
    """Vehicles/operators/geofences return the generated reference data."""
    client = TestClient(app)
    assert len(client.get("/v1/fleet/vehicles", headers=AUTH).json()["data"]) == 40
    assert len(client.get("/v1/fleet/geofences", headers=AUTH).json()["data"]) == 9
