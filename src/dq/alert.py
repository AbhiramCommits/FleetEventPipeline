"""Data-quality alerting.

Failed gates produce (a) a Markdown alert under ``reports/alerts/<run_id>.md``,
(b) a GitHub Actions ``::error::`` annotation on stdout, and (c) optionally a
POST to a pluggable webhook sink (``ALERT_WEBHOOK``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from src.common.config import get_settings
from src.common.logging import get_logger, log_event

logger = get_logger("fleet.dq.alert")


def render_markdown(run_id: str, failures: list[dict[str, Any]]) -> str:
    """Render the alert body as Markdown."""
    lines = [
        f"# Data-quality alert",
        "",
        f"- **run_id:** `{run_id}`",
        f"- **generated_at:** {datetime.now(timezone.utc).isoformat()}",
        f"- **failed checks:** {len(failures)}",
        "",
        "| check | severity | observed | threshold | detail |",
        "| --- | --- | --- | --- | --- |",
    ]
    for f in failures:
        lines.append(
            f"| {f['check_name']} | {f['severity']} | {f['observed']} | {f['threshold']} | {f['detail']} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_alert(run_id: str, failures: list[dict[str, Any]]) -> Path:
    """Write the Markdown alert and emit a GitHub annotation. Returns the path."""
    alerts_dir = get_settings().repo_root / "reports" / "alerts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    path = alerts_dir / f"{run_id}.md"
    path.write_text(render_markdown(run_id, failures))

    for f in failures:
        # GitHub Actions annotation format.
        print(
            f"::error title=DQ gate {f['check_name']}::"
            f"observed={f['observed']} threshold={f['threshold']} {f['detail']}"
        )

    webhook = get_settings().alert_webhook
    if webhook:
        try:
            httpx.post(webhook, json={"run_id": run_id, "failures": failures}, timeout=10.0)
            log_event(logger, "alert webhook sent", webhook=webhook)
        except Exception as exc:  # noqa: BLE001
            log_event(logger, "alert webhook failed", error=str(exc))

    log_event(logger, "alert written", path=str(path), failures=len(failures))
    return path


def dumps(failures: list[dict[str, Any]]) -> str:
    """Serialize failures for logging."""
    return json.dumps(failures, default=str)
