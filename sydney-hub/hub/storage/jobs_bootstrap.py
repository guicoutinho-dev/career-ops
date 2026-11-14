"""Isolated SQLite database bootstrapper with strict path guard and ?mode=ro copy."""
from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from hub.storage.db import (
    ISOLATED_DB_MARKER,
    _DEFAULT_ISOLATED_ROOT,
    ensure_published_schema,
    validate_isolated_jobs_db_path,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bootstrap_isolated_jobs_db(
    source_db: Path | None,
    dest_db: Path,
    *,
    isolated_root: Path | None = None,
) -> dict[str, object]:
    """Create a fresh isolated Jobs DB or read-only backup copy inside isolated_root.

    Validates `dest_db` before creating directories, marker files, or opening SQLite,
    and unconditionally refuses pre-existing targets or `source_db == dest_db`.
    """
    validated_dest = validate_isolated_jobs_db_path(
        dest_db,
        isolated_root=isolated_root,
        require_marker=False,
    )
    if source_db is not None:
        src_resolved = Path(os.path.realpath(str(source_db)))
        if src_resolved == validated_dest:
            raise ValueError(f"Destination path must not match source database: {validated_dest}")
    if os.path.lexists(dest_db) or os.path.lexists(validated_dest):
        raise FileExistsError(f"Isolated destination DB already exists: {validated_dest}")

    resolved_root = Path(os.path.realpath(str(isolated_root if isolated_root is not None else _DEFAULT_ISOLATED_ROOT)))

    source_sha: str | None = None
    if source_db is not None:
        src_path = Path(source_db)
        if not src_path.is_file():
            raise FileNotFoundError(f"Source SQLite database not found: {src_path}")
        src_resolved = Path(os.path.realpath(str(src_path)))
        if src_resolved == validated_dest:
            raise ValueError(f"Destination path must not match source database: {validated_dest}")
        source_sha = _sha256(src_resolved)

        resolved_root.mkdir(parents=True, exist_ok=True)
        marker = resolved_root / ISOLATED_DB_MARKER
        if not marker.exists():
            marker.write_text("sydneystays-jobs-isolated\n", encoding="utf-8")
        validated_dest.parent.mkdir(parents=True, exist_ok=True)

        source_uri = f"{src_resolved.as_uri()}?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True)) as src_conn:
            if src_conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError(f"Source database failed integrity check: {src_resolved}")
            with closing(sqlite3.connect(validated_dest)) as dst_conn:
                src_conn.backup(dst_conn)
    else:
        resolved_root.mkdir(parents=True, exist_ok=True)
        marker = resolved_root / ISOLATED_DB_MARKER
        if not marker.exists():
            marker.write_text("sydneystays-jobs-isolated\n", encoding="utf-8")
        validated_dest.parent.mkdir(parents=True, exist_ok=True)

    version = ensure_published_schema(validated_dest, isolated_root=resolved_root)
    return {
        "dest_db": str(validated_dest),
        "source_sha256": source_sha,
        "schema_version": version,
        "integrity": "ok",
    }
