"""
Auto-migrate: runs SQL files on startup that haven't been applied yet.
Creates a _migrations tracking table to ensure idempotency.
"""

import os
import hashlib
import logging
from pathlib import Path
from sqlalchemy import create_engine, text

logger = logging.getLogger("ielts.migrate")


def run_migrations(database_url: str):
    engine = create_engine(database_url)
    with engine.connect() as conn:
        # Ensure tracking table exists
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS _migrations ("
            "  filename TEXT PRIMARY KEY,"
            "  checksum TEXT NOT NULL,"
            "  applied_at TIMESTAMPTZ DEFAULT NOW()"
            ")"
        ))
        conn.commit()

        migrations_dir = Path(__file__).resolve().parent.parent.parent / "migrations"
        if not migrations_dir.exists():
            logger.warning(f"Migrations directory not found: {migrations_dir}")
            return

        for fpath in sorted(migrations_dir.glob("*.sql")):
            sql = fpath.read_text()
            cs = hashlib.md5(sql.encode()).hexdigest()
            fn = fpath.name

            row = conn.execute(
                text("SELECT checksum FROM _migrations WHERE filename = :fn"),
                {"fn": fn},
            ).fetchone()

            if row and row[0] == cs:
                logger.info(f"  SKIP {fn} (already applied)")
                continue

            logger.info(f"  APPLY {fn}")
            conn.execute(text(sql))
            conn.execute(
                text(
                    "INSERT INTO _migrations (filename, checksum) VALUES (:fn, :cs) "
                    "ON CONFLICT (filename) DO UPDATE SET checksum = EXCLUDED.checksum, applied_at = NOW()"
                ),
                {"fn": fn, "cs": cs},
            )
            conn.commit()

    logger.info("Migrations complete.")
    engine.dispose()
