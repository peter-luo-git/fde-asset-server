"""Exact database ownership journal shared by the smoke parent and pytest fixtures."""

import os
from pathlib import Path
import re
import sys
import uuid


def ownership():
    prefix = os.environ.get("FDE_ASSET_TEST_DATABASE_PREFIX")
    manifest = os.environ.get("FDE_ASSET_TEST_DATABASE_MANIFEST")
    if not prefix and not manifest:
        return None, None
    if not prefix or not manifest or not re.fullmatch(r"asset_smoke_[0-9a-f]{32}_", prefix):
        raise ValueError("invalid smoke database ownership")
    directory = Path(manifest)
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("invalid smoke database manifest")
    return prefix, directory


def register_database():
    """Persist ownership before CREATE DATABASE, including interrupted creates."""
    prefix, directory = ownership()
    name = (prefix or "asset_test_") + uuid.uuid4().hex[:12]
    marker = directory / name if directory else None
    if marker is not None:
        marker.touch(mode=0o600, exist_ok=False)
    return name, marker


def cleanup():
    prefix, directory = ownership()
    if directory is None:
        raise ValueError("smoke cleanup requires parent ownership")
    markers = list(directory.iterdir())
    # Validate every entry before making any destructive request. Never enumerate PG databases.
    if any(
        marker.is_symlink()
        or not marker.is_file()
        or not re.fullmatch(re.escape(prefix) + r"[0-9a-f]{12}", marker.name)
        for marker in markers
    ):
        raise ValueError("foreign entry in smoke database manifest")
    if not markers:
        return
    import psycopg
    from psycopg import sql

    url = os.environ["FDE_ASSET_TEST_DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    with psycopg.connect(
        url,
        autocommit=True,
        connect_timeout=1,
        options="-c statement_timeout=1000 -c lock_timeout=1000",
    ) as connection:
        for marker in markers:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(marker.name)
                )
            )
            marker.unlink()


if __name__ == "__main__":
    try:
        cleanup()
    except Exception:
        # Credentials and driver connection errors must never enter the report.
        print("Asset owned database cleanup failed; smoke cannot pass.", file=sys.stderr)
        raise SystemExit(1)
