"""HTTP client for the telematics vendor API.

Wraps ``httpx`` with bearer authentication, tenacity retry (exponential backoff
with jitter on 429/5xx/timeouts, max 5 attempts) and a cursor-following
``paginate`` generator that matches the vendor's page contract.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from src.common.config import get_settings
from src.common.logging import get_logger, log_event

logger = get_logger("fleet.ingest.client")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RetryableStatus(Exception):
    """Raised for HTTP statuses that should trigger a retry."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"retryable status {status_code}")
        self.status_code = status_code


@dataclass
class ClientStats:
    """Aggregated transport statistics for one client instance."""

    requests: int = 0
    retries: int = 0
    retryable_statuses: list[int] = field(default_factory=list)


class FleetApiClient:
    """Thin, retrying client for the mock/real fleet vendor."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        max_attempts: int = 5,
        page_size: int | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.fleet_api_base).rstrip("/")
        self.page_size = page_size or settings.page_size
        self.max_attempts = max_attempts
        self.stats = ClientStats()
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {token or settings.fleet_api_token}"},
        )

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._client.close()

    def __enter__(self) -> "FleetApiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _request(self, path: str, params: dict[str, Any]) -> httpx.Response:
        """Perform one GET attempt, surfacing retryable statuses as exceptions."""
        self.stats.requests += 1
        resp = self._client.get(path, params=params)
        if resp.status_code in RETRYABLE_STATUS:
            self.stats.retryable_statuses.append(resp.status_code)
            raise RetryableStatus(resp.status_code)
        resp.raise_for_status()
        return resp

    def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """GET with tenacity retry/backoff. Raises after ``max_attempts``."""
        retryer = Retrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential_jitter(multiplier=0.1, max=3.0),
            retry=retry_if_exception_type((RetryableStatus, httpx.TimeoutException, httpx.TransportError)),
            reraise=True,
            before_sleep=self._on_retry,
        )
        return retryer(self._request, path, params or {})

    def _on_retry(self, retry_state: Any) -> None:
        self.stats.retries += 1
        log_event(
            logger,
            "retrying vendor request",
            attempt=retry_state.attempt_number,
            sleep=round(getattr(retry_state.next_action, "sleep", 0.0), 3),
        )

    def paginate(
        self,
        endpoint: str,
        after: str | None = None,
        limit: int | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield one page at a time, following ``end_cursor`` until exhausted.

        Each yielded dict has keys ``data``, ``end_cursor``, ``has_next``,
        ``latency_ms`` and ``status``.
        """
        limit = limit or self.page_size
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": limit}
            if after:
                params["after"] = after
            if cursor:
                params["cursor"] = cursor
            start = time.perf_counter()
            resp = self.get(endpoint, params)
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            body = resp.json()
            pagination = body.get("pagination", {})
            has_next = bool(pagination.get("has_next"))
            end_cursor = pagination.get("end_cursor")
            yield {
                "data": body.get("data", []),
                "end_cursor": end_cursor,
                "has_next": has_next,
                "latency_ms": latency_ms,
                "status": resp.status_code,
            }
            if not has_next:
                break
            cursor = end_cursor
            if cursor is None:
                break

    def fetch_reference(self, endpoint: str) -> list[dict[str, Any]]:
        """Fetch a non-paginated (or single-page) reference collection."""
        resp = self.get(endpoint, {})
        return resp.json().get("data", [])
