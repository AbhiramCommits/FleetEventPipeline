"""Apply every ``.sql`` migration in filename order (idempotent).

Usage::

    python -m src.common.migrate

Files are discovered in ``sql/`` sorted lexicographically and, if present,
``powerbi/views.sql`` is applied last. Every script is written to be safe to run
repeatedly (``IF NOT EXISTS`` / ``CREATE OR REPLACE`` / truncate-and-reload), so
running migrate twice is a no-op in terms of final state.
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.common.config import get_settings
from src.common.db import get_conn
from src.common.logging import get_logger, log_event

logger = get_logger("fleet.migrate")

BOOTSTRAP = """
CREATE SCHEMA IF NOT EXISTS ctl;
CREATE TABLE IF NOT EXISTS ctl.schema_migration (
    filename   text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
"""


def discover_migrations() -> list[Path]:
    """Return migration files in application order."""
    root = get_settings().repo_root
    files = sorted((root / "sql").glob("*.sql"))
    views = root / "powerbi" / "views.sql"
    if views.exists():
        files.append(views)
    return files


def migrate() -> list[str]:
    """Apply all migrations and return the list of filenames applied."""
    applied: list[str] = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(BOOTSTRAP)
        conn.commit()
        for path in discover_migrations():
            sql = path.read_text()
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    """
                    INSERT INTO ctl.schema_migration (filename)
                    VALUES (%s)
                    ON CONFLICT (filename) DO UPDATE SET applied_at = now()
                    """,
                    (path.name,),
                )
            conn.commit()
            applied.append(path.name)
            log_event(logger, "migration applied", file=path.name)
    return applied


def main() -> int:
    """CLI entrypoint."""
    applied = migrate()
    log_event(logger, "migrations complete", files=applied, count=len(applied))
    return 0


if __name__ == "__main__":
    sys.exit(main())
