"""SQLite access. This package is the only place that imports sqlite3."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    fb_group_id TEXT,
    name TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at REAL,
    last_status TEXT,
    last_new_posts INTEGER NOT NULL DEFAULT 0,
    created_at REAL
);
CREATE TABLE IF NOT EXISTS posts (
    post_id TEXT PRIMARY KEY,
    group_id INTEGER,
    group_url TEXT NOT NULL,
    group_name TEXT,
    permalink TEXT NOT NULL,
    author TEXT,
    author_url TEXT,
    body TEXT NOT NULL,
    created_ts REAL NOT NULL,
    photos TEXT NOT NULL DEFAULT '[]',
    comment_count INTEGER NOT NULL DEFAULT 0,
    reactions INTEGER NOT NULL DEFAULT 0,
    first_seen_at REAL NOT NULL,
    kind TEXT NOT NULL,
    classification TEXT NOT NULL,
    score INTEGER NOT NULL DEFAULT 0,
    ai_status TEXT NOT NULL DEFAULT 'pending',
    ai_json TEXT,
    is_new INTEGER NOT NULL DEFAULT 1,
    user_status TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_posts_kind_created ON posts(kind, created_ts DESC);
CREATE INDEX IF NOT EXISTS idx_posts_ai_status ON posts(ai_status);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    finished_at REAL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    groups_ok INTEGER NOT NULL DEFAULT 0,
    groups_failed INTEGER NOT NULL DEFAULT 0,
    new_posts INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
"""

CATALOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS employers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    website TEXT,
    location TEXT
);
CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    employer_id TEXT REFERENCES employers(id),
    title TEXT,
    location TEXT,
    suburb TEXT,
    state TEXT,
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_group_name TEXT,
    source_group_url TEXT,
    verification_state TEXT NOT NULL CHECK (verification_state IN ('verified_job','unverified_job_post','employer_lead')),
    first_seen_at REAL NOT NULL,
    published_at TEXT,
    last_verified_at TEXT,
    description TEXT,
    employment_type TEXT,
    experience_required TEXT,
    requirements TEXT,
    pay_display TEXT,
    pay_hourly_min REAL,
    visa_status TEXT NOT NULL DEFAULT 'unknown'
);
CREATE TABLE IF NOT EXISTS opportunity_evidence (
    id INTEGER PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
    source_url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    observed_at REAL NOT NULL,
    checked_at REAL,
    excerpt TEXT,
    supports_job INTEGER NOT NULL DEFAULT 0,
    UNIQUE(opportunity_id, source_url, source_type)
);
CREATE TABLE IF NOT EXISTS opportunity_commutes (
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
    origin_suburb TEXT NOT NULL,
    transit_minutes INTEGER NOT NULL CHECK (transit_minutes >= 0),
    checked_at REAL NOT NULL,
    source_url TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, origin_suburb)
);
CREATE INDEX IF NOT EXISTS idx_opportunities_state ON opportunities(verification_state, first_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_opportunities_suburb ON opportunities(suburb);
"""


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rollback_manifest(path: Path, target_version: int = 0) -> Path:
    if target_version not in (0, 1, 2):
        raise ValueError('Rollback target must be schema version 0, 1, or 2')
    suffix = {0: '.catalog-rollback.json', 1: '.catalog-v2-rollback.json',
              2: '.employer-pilot-rollback.json'}[target_version]
    return path.with_name(path.name + suffix)


def _backup(path: Path, label: str = 'pre-catalog') -> Path:
    backup = path.with_name(f'{path.name}.{label}-{time.time_ns()}.db')
    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
    with closing(sqlite3.connect(backup)) as check:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            backup.unlink(missing_ok=True)
            raise RuntimeError("Catalog backup failed integrity check")
    return backup


def snapshot_catalog_for_pilot(path: Path) -> Path:
    """Keep one verified pre-pilot snapshot until explicitly restored."""
    path = Path(path)
    if not path.is_file():
        raise ValueError('Pilot database is missing')
    manifest = _rollback_manifest(path, 2)
    if manifest.exists():
        info = json.loads(manifest.read_text(encoding='utf-8'))
        existing = Path(info['backup'])
        if existing.is_file() and _digest(existing) == info['before_sha256']:
            return existing
        raise ValueError('Existing pilot backup is missing or changed')
    with closing(sqlite3.connect(path)) as conn:
        if conn.execute('PRAGMA user_version').fetchone()[0] != 2:
            raise ValueError('Pilot requires catalog schema version 2')
    backup = _backup(path, 'pre-employer-pilot')
    manifest.write_text(json.dumps({
        'backup': str(backup.resolve()), 'before_sha256': _digest(backup),
        'after_sha256': _digest(path),
    }), encoding='utf-8')
    return backup


def restore_catalog_backup(path: str | Path, *, force: bool = False, target_version: int = 0) -> Path | None:
    """Restore a pre-migration database, preserving newer writes in a safety copy."""
    path = Path(path)
    manifest = _rollback_manifest(path, target_version)
    info = json.loads(manifest.read_text(encoding="utf-8"))
    backup = Path(info["backup"])
    if backup.parent.resolve() != path.parent.resolve() or not backup.is_file():
        raise ValueError("Catalog backup is missing or outside the database directory")
    if _digest(backup) != info["before_sha256"]:
        raise ValueError("Catalog backup changed since migration")
    with closing(sqlite3.connect(backup)) as check:
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Catalog backup failed integrity check")
    with closing(sqlite3.connect(path, timeout=0)) as current:
        current.execute('PRAGMA locking_mode=EXCLUSIVE')
        current.execute('BEGIN EXCLUSIVE')
        current.commit()  # EXCLUSIVE locking mode keeps writers out through backup.
        if any(
            sidecar.exists() and sidecar.stat().st_size for sidecar in (Path(str(path) + '-wal'), Path(str(path) + '-shm'))
        ):
            raise ValueError('Database has active SQLite sidecars; stop the service before rollback')
        changed = _digest(path) != info['after_sha256']
        if changed and not force:
            raise ValueError('Database changed since migration; rollback would erase newer writes')
        preserved = None
        if changed:
            preserved = path.with_name(f'{path.name}.before-rollback-{time.time_ns()}.db')
            with closing(sqlite3.connect(preserved)) as safety:
                current.backup(safety)
                if safety.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Rollback safety backup failed integrity check')
        with closing(sqlite3.connect(backup)) as source:
            source.backup(current)
        if current.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('Restored database failed integrity check')
    manifest.unlink()
    return preserved


class Database:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        database_path = Path(self._path)
        existed = database_path.is_file()
        version = 0
        if existed:
            with closing(sqlite3.connect(self._path)) as probe:
                version = probe.execute("PRAGMA user_version").fetchone()[0]
        backup_target = 0 if version < 1 else 1 if version == 1 else None
        backup = _backup(database_path, 'pre-catalog-v2' if version == 1 else 'pre-catalog') if existed and backup_target is not None else None
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for statement in (SCHEMA + CATALOG_SCHEMA).split(";"):
                if statement.strip():
                    conn.execute(statement)
            if version < 2:
                for table, column, definition in (
                    ('opportunities', 'source_group_name', 'TEXT'),
                    ('opportunities', 'source_group_url', 'TEXT'),
                    ('opportunity_evidence', 'checked_at', 'REAL'),
                ):
                    if column not in {row[1] for row in conn.execute(f'PRAGMA table_info({table})')}:
                        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
            if version < 1:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(posts)")}
                if "legacy_ai_json" not in columns:
                    conn.execute("ALTER TABLE posts ADD COLUMN legacy_ai_json TEXT")
                if "legacy_classification_json" not in columns:
                    conn.execute("ALTER TABLE posts ADD COLUMN legacy_classification_json TEXT")
                for row in conn.execute(
                    "SELECT post_id,kind,classification,ai_json FROM posts WHERE ai_status='approved'"
                ).fetchall():
                    try:
                        old = json.loads(row["classification"])
                    except (TypeError, ValueError):
                        old = {}
                    safe = {"kind": row["kind"], "score": 0}
                    if isinstance(old, dict) and isinstance(old.get("suburb"), str):
                        safe["suburb"] = old["suburb"]
                    conn.execute(
                        "UPDATE posts SET ai_status='skipped', ai_json=NULL, legacy_ai_json=?, "
                        "legacy_classification_json=?, classification=? WHERE post_id=?",
                        (row["ai_json"], row["classification"], json.dumps(safe), row["post_id"]),
                    )
            if version < 2:
                conn.execute('PRAGMA user_version=2')
            conn.execute("CREATE TRIGGER IF NOT EXISTS reject_legacy_approved_insert BEFORE INSERT ON posts "
                         "WHEN NEW.ai_status='approved' BEGIN SELECT RAISE(ABORT, 'approved legacy records are unsupported'); END")
            conn.execute("CREATE TRIGGER IF NOT EXISTS reject_legacy_approved_update BEFORE UPDATE ON posts "
                         "WHEN NEW.ai_status='approved' BEGIN SELECT RAISE(ABORT, 'approved legacy records are unsupported'); END")
        if backup:
            _rollback_manifest(database_path, backup_target).write_text(json.dumps({
                "backup": str(backup.resolve()),
                "before_sha256": _digest(backup),
                "after_sha256": _digest(database_path),
            }), encoding="utf-8")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
