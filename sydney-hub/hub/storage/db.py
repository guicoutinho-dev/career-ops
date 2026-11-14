"""SQLite access. This package is the only place that imports sqlite3."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator

ISOLATED_DB_MARKER = ".sydneystays-jobs-isolated"
_DEFAULT_ISOLATED_ROOT = Path(__file__).resolve().parent.parent.parent / "data" / "jobs_isolated"
_FORBIDDEN_EXACT_PATHS = {
    os.path.normcase(os.path.normpath(r"C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops\sydney-hub\data\hub.db")),
    os.path.normcase(os.path.normpath(r"D:\AntigravityProjetos\career-ops\sydney-hub\data\hub.db")),
}
_FORBIDDEN_ANCESTOR_PREFIXES = (
    os.path.normcase(os.path.normpath(r"C:\Users\guicx\.codex\worktrees")),
    os.path.normcase(os.path.normpath(r"D:\AntigravityProjetos\career-ops")),
)

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
    visa_status TEXT NOT NULL DEFAULT 'unknown',
    category TEXT,
    lat REAL,
    lng REAL,
    discovery_source TEXT,
    ats_provider TEXT,
    role_bucket TEXT,
    suburb_match_basis TEXT
);
CREATE TABLE IF NOT EXISTS opportunity_evidence (
    id INTEGER PRIMARY KEY,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
    source_url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    observed_at REAL NOT NULL,
    checked_at REAL,
    excerpt TEXT,
    check_outcome TEXT,
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

PUBLISHED_SCHEMA = """
CREATE TABLE IF NOT EXISTS published_source_keys (
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
    PRIMARY KEY (source_id, external_id)
);
CREATE TABLE IF NOT EXISTS commute_observations (
    opportunity_id TEXT NOT NULL REFERENCES opportunities(id),
    origin_label TEXT NOT NULL,
    departure_window TEXT NOT NULL,
    departure_at TEXT NOT NULL,
    transit_minutes INTEGER NOT NULL CHECK (transit_minutes >= 0),
    checked_at REAL NOT NULL,
    source_url TEXT NOT NULL,
    PRIMARY KEY (opportunity_id, origin_label, departure_window)
);
"""


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rollback_manifest(path: Path, target_version: int = 0) -> Path:
    if target_version not in (0, 1, 2, 3, 4, 5, 6):
        raise ValueError('Rollback target must be schema version 0, 1, 2, 3, 4, 5, or 6')
    suffix = {0: '.catalog-rollback.json', 1: '.catalog-v2-rollback.json',
              2: '.catalog-v3-rollback.json', 3: '.catalog-v4-rollback.json',
              4: '.employer-pilot-rollback.json', 5: '.published-v5-rollback.json',
              6: '.published-v6-rollback.json'}[target_version]
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


def migrate_published_catalog(path: str | Path) -> Path:
    """Opt-in v4→v5 migration; v4 employer pilot snapshots remain independent."""
    path = Path(path)
    if not path.is_file():
        raise ValueError('Catalog database is missing')
    manifest = _rollback_manifest(path, 5)
    with closing(sqlite3.connect(path)) as conn:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version == 5:
        if not manifest.is_file():
            raise ValueError('Published schema exists without its rollback manifest')
        backup = Path(json.loads(manifest.read_text(encoding='utf-8'))['backup'])
        if not backup.is_file():
            raise ValueError('Published catalog backup is missing')
        return backup
    if version != 4:
        raise ValueError('Published migration requires catalog schema version 4')
    backup = _backup(path, 'pre-published-v5')
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        try:
            for statement in PUBLISHED_SCHEMA.split(';'):
                if statement.strip():
                    conn.execute(statement)
            conn.execute('PRAGMA user_version=5')
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    temp = manifest.with_name(manifest.name + '.tmp')
    try:
        temp.write_text(json.dumps({
            'backup': str(backup.resolve()), 'before_sha256': _digest(backup),
            'after_sha256': _digest(path),
        }), encoding='utf-8')
        temp.replace(manifest)
    except Exception:
        temp.unlink(missing_ok=True)
        with closing(sqlite3.connect(backup)) as source, closing(sqlite3.connect(path)) as target:
            source.backup(target)
        raise
    return backup


def restore_published_catalog_backup(path: str | Path, *, force: bool = False) -> Path | None:
    return restore_catalog_backup(path, force=force, target_version=5)


def _apply_lifecycle_v6_columns(conn: sqlite3.Connection) -> None:
    opp_cols = {row[1] for row in conn.execute("PRAGMA table_info(opportunities)")}
    for column, definition in (
        ("lifecycle_state", "TEXT NOT NULL DEFAULT 'active' CHECK (lifecycle_state IN ('active','stale','expired'))"),
        ("stale_since", "TEXT"),
        ("expired_at", "TEXT"),
        ("consecutive_misses", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if column not in opp_cols:
            conn.execute(f"ALTER TABLE opportunities ADD COLUMN {column} {definition}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_opportunities_lifecycle ON opportunities(lifecycle_state, verification_state)")


def migrate_lifecycle_catalog(path: str | Path) -> Path:
    """Opt-in v5->v6 migration adding conservative lifecycle/expiry columns with rollback."""
    path = Path(path)
    if not path.is_file():
        raise ValueError("Catalog database is missing")
    manifest = _rollback_manifest(path, 6)
    with closing(sqlite3.connect(path)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 6:
        if not manifest.is_file():
            raise ValueError("Lifecycle schema exists without its rollback manifest")
        backup = Path(json.loads(manifest.read_text(encoding="utf-8"))["backup"])
        if not backup.is_file():
            raise ValueError("Lifecycle catalog backup is missing")
        return backup
    if version != 5:
        raise ValueError("Lifecycle migration requires catalog schema version 5")
    backup = _backup(path, "pre-lifecycle-v6")
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            _apply_lifecycle_v6_columns(conn)
            conn.execute("PRAGMA user_version=6")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    temp = manifest.with_name(manifest.name + ".tmp")
    try:
        temp.write_text(json.dumps({
            "backup": str(backup.resolve()),
            "before_sha256": _digest(backup),
            "after_sha256": _digest(path),
        }), encoding="utf-8")
        temp.replace(manifest)
    except Exception:
        temp.unlink(missing_ok=True)
        with closing(sqlite3.connect(backup)) as source, closing(sqlite3.connect(path)) as target:
            source.backup(target)
        raise
    return backup


def snapshot_catalog_for_pilot(path: Path) -> Path:
    """Keep one verified pre-pilot v4 snapshot until explicitly restored."""
    path = Path(path)
    if not path.is_file():
        raise ValueError('Pilot database is missing')
    with closing(sqlite3.connect(path)) as conn:
        if conn.execute('PRAGMA user_version').fetchone()[0] != 4:
            raise ValueError('Pilot requires catalog schema version 4')
    manifest = _rollback_manifest(path, 4)
    if manifest.exists():
        info = json.loads(manifest.read_text(encoding='utf-8'))
        existing = Path(info['backup'])
        if existing.is_file() and _digest(existing) == info.get('before_sha256') and _digest(path) == info.get('after_sha256'):
            with closing(sqlite3.connect(existing)) as conn:
                if conn.execute('PRAGMA user_version').fetchone()[0] == 4:
                    return existing
        manifest.replace(manifest.with_name(f'{manifest.name}.superseded-{time.time_ns()}'))
    backup = _backup(path, 'pre-employer-pilot')
    manifest.write_text(json.dumps({
        'backup': str(backup.resolve()), 'before_sha256': _digest(backup),
        'after_sha256': _digest(path),
    }), encoding='utf-8')
    return backup


def _has_active_sidecars(path: Path) -> bool:
    return any(
        sidecar.exists() and sidecar.stat().st_size > 0
        for sidecar in (Path(str(path) + "-wal"), Path(str(path) + "-shm"))
    )


def validate_isolated_jobs_db_path(
    db_path: str | Path,
    *,
    isolated_root: Path | None = None,
    require_marker: bool = True,
) -> Path:
    """Validate an isolated Jobs SQLite target BEFORE opening SQLite or creating sidecars.

    Rejects known V4/main databases, `hub.db` filenames, symlink escapes, traversal
    outside `isolated_root`, and directories lacking `.sydneystays-jobs-isolated`.
    """
    raw_str = str(db_path).strip()
    if not raw_str:
        raise ValueError("Isolated Jobs DB path cannot be empty")
    candidate = Path(raw_str)
    normalized_raw = os.path.normcase(os.path.abspath(raw_str))
    normalized_real = os.path.normcase(os.path.realpath(raw_str))
    for check_norm in (normalized_raw, normalized_real):
        if check_norm in _FORBIDDEN_EXACT_PATHS:
            raise PermissionError(f"Refusing protected V4/main database path: {db_path}")
        if check_norm.endswith(os.sep + "data" + os.sep + "hub.db"):
            raise PermissionError(f"Refusing shared sydney-hub/data/hub.db path: {db_path}")

    root = Path(isolated_root) if isolated_root is not None else _DEFAULT_ISOLATED_ROOT
    resolved_root = Path(os.path.realpath(str(root)))
    norm_root = os.path.normcase(str(resolved_root))

    # Reject symlinks anywhere along root path and enforce checkout/temp-only anchoring.
    cursor = root
    while cursor != cursor.parent:
        if cursor.exists() and os.path.islink(cursor):
            raise PermissionError(f"Symlink component rejected in isolated DB root: {cursor}")
        cursor = cursor.parent

    if any(norm_root == prefix or norm_root.startswith(prefix + os.sep) for prefix in _FORBIDDEN_ANCESTOR_PREFIXES):
        raise PermissionError(f"Refusing protected V4/main isolated_root: {isolated_root}")
    lowered_norm = norm_root.replace("\\", "/")
    if (
        "sydneystays-employer-pilot" in lowered_norm
        or ("career-ops/sydney-hub/data" in lowered_norm and "jobs_isolated" not in lowered_norm)
    ):
        raise PermissionError(f"Refusing protected V4/main directory as isolated_root: {isolated_root}")
    if (resolved_root / "hub.db").exists():
        raise PermissionError(f"Refusing isolated_root containing shared hub.db: {resolved_root}")

    anchor_root = Path(os.path.realpath(str(_DEFAULT_ISOLATED_ROOT)))
    temp_root = Path(os.path.realpath(tempfile.gettempdir()))
    in_anchor = resolved_root == anchor_root or resolved_root.is_relative_to(anchor_root)
    in_temp = resolved_root != temp_root and resolved_root.is_relative_to(temp_root)
    if not (in_anchor or in_temp):
        raise PermissionError(
            f"Isolated DB root {resolved_root} must stay inside {anchor_root} (or pytest/system temp)"
        )
    if os.path.islink(candidate):
        raise PermissionError(f"Isolated DB path cannot be a symlink: {db_path}")

    resolved_target = Path(normalized_real)
    if resolved_target == resolved_root or not resolved_target.is_relative_to(resolved_root):
        raise PermissionError(
            f"Database path {resolved_target} must resolve strictly inside isolated root {resolved_root}"
        )
    if resolved_target.name.lower() == "hub.db":
        raise PermissionError(f"Isolated Jobs DB must not use shared filename hub.db: {db_path}")
    if candidate.exists() and candidate.is_dir():
        raise ValueError(f"Isolated Jobs DB path points to a directory: {db_path}")

    # Walk existing parents between candidate and resolved_root to reject nested symlinks.
    cursor = candidate.parent
    while cursor != cursor.parent:
        if cursor.exists() and os.path.islink(cursor):
            raise PermissionError(f"Symlink component rejected in isolated DB path: {cursor}")
        if Path(os.path.realpath(str(cursor))) == resolved_root:
            break
        cursor = cursor.parent

    if require_marker and not (resolved_root / ISOLATED_DB_MARKER).is_file():
        raise PermissionError(
            f"Isolated DB directory {resolved_root} is missing required marker {ISOLATED_DB_MARKER}"
        )
    return resolved_target


def check_isolated_jobs_db_ready(
    path: str | Path,
    *,
    isolated_root: Path | None = None,
) -> tuple[bool, str | None]:
    """Read-only check verifying isolated path guard, schema v5, and SQLite integrity."""
    try:
        validated = validate_isolated_jobs_db_path(path, isolated_root=isolated_root)
    except (ValueError, PermissionError):
        return False, "db_outside_isolated_root"
    if not validated.is_file():
        return False, "db_missing"
    uri = f"{validated.as_uri()}?mode=ro"
    try:
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (5, 6):
                return False, f"db_wrong_schema_version_{version}"
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                return False, "db_integrity_failed"
    except sqlite3.Error:
        return False, "db_unreadable"
    return True, None


def ensure_published_schema(path: str | Path, *, isolated_root: Path | None = None) -> int:
    """Ensure schema v5 on a validated isolated Jobs DB without requiring legacy manifests."""
    validated = validate_isolated_jobs_db_path(path, isolated_root=isolated_root)
    if not validated.is_file():
        Database(validated)
    with closing(sqlite3.connect(validated)) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < 4:
        Database(validated)
        with closing(sqlite3.connect(validated)) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 6:
        return 6
    if version not in (4, 5):
        raise ValueError(f"Unsupported catalog schema version {version} for published Jobs")
    with closing(sqlite3.connect(validated)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in PUBLISHED_SCHEMA.split(";"):
                if statement.strip():
                    conn.execute(statement)
            if version == 4:
                conn.execute("PRAGMA user_version=5")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Isolated Jobs database failed integrity check")
    return 5


def ensure_lifecycle_schema(path: str | Path, *, isolated_root: Path | None = None) -> int:
    """Ensure schema v6 (lifecycle & expiry) on a validated isolated Jobs DB."""
    validated = validate_isolated_jobs_db_path(path, isolated_root=isolated_root)
    version = ensure_published_schema(validated, isolated_root=isolated_root)
    if version == 6:
        return 6
    with closing(sqlite3.connect(validated)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            _apply_lifecycle_v6_columns(conn)
            conn.execute("PRAGMA user_version=6")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Isolated Jobs database failed integrity check")
    return 6


def _pre_run_manifest_path(path: Path) -> Path:
    return path.with_name(path.name + ".published-run-rollback.json")


def snapshot_published_run(path: str | Path, *, isolated_root: Path | None = None) -> Path:
    """Capture an isolated pre-run SQLite backup + manifest before any live Jobs write."""
    validated = validate_isolated_jobs_db_path(path, isolated_root=isolated_root)
    if not validated.is_file():
        raise ValueError("Isolated Jobs database does not exist yet")
    if _has_active_sidecars(validated):
        raise ValueError("Database has active SQLite sidecars; stop the service before snapshot")
    backup = _backup(validated, "pre-published-run")
    manifest = _pre_run_manifest_path(validated)
    temp = manifest.with_name(manifest.name + ".tmp")
    digest = _digest(backup)
    temp.write_text(
        json.dumps(
            {
                "backup": str(backup.resolve()),
                "before_sha256": digest,
                "after_sha256": _digest(validated),
            }
        ),
        encoding="utf-8",
    )
    temp.replace(manifest)
    return backup


def _restore_from_manifest(path: Path, manifest: Path, *, force: bool) -> Path | None:
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
        current.execute("PRAGMA locking_mode=EXCLUSIVE")
        current.execute("BEGIN EXCLUSIVE")
        current.commit()
        if _has_active_sidecars(path):
            raise ValueError("Database has active SQLite sidecars; stop the service before rollback")
        changed = _digest(path) != info["after_sha256"]
        if changed and not force:
            raise ValueError("Database changed since migration; rollback would erase newer writes")
        preserved = None
        if changed:
            preserved = path.with_name(f"{path.name}.before-rollback-{time.time_ns()}.db")
            with closing(sqlite3.connect(preserved)) as safety:
                current.backup(safety)
                if safety.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Rollback safety backup failed integrity check")
        with closing(sqlite3.connect(backup)) as source:
            source.backup(current)
        if current.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Restored database failed integrity check")
    manifest.unlink()
    return preserved


def restore_published_run(
    path: str | Path,
    *,
    isolated_root: Path | None = None,
    force: bool = False,
) -> Path | None:
    """Restore an isolated Jobs DB to its pre-run snapshot."""
    validated = validate_isolated_jobs_db_path(path, isolated_root=isolated_root)
    return _restore_from_manifest(validated, _pre_run_manifest_path(validated), force=force)


def restore_catalog_backup(path: str | Path, *, force: bool = False, target_version: int = 0) -> Path | None:
    """Restore a pre-migration database, preserving newer writes in a safety copy."""
    path = Path(path)
    manifest = _rollback_manifest(path, target_version)
    if target_version == 3 and not manifest.exists():
        legacy = _rollback_manifest(path, 4)
        if legacy.exists():
            manifest = legacy
    return _restore_from_manifest(path, manifest, force=force)


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
        backup_target = 0 if version < 1 else 1 if version == 1 else 2 if version == 2 else 3 if version == 3 else None
        backup_label = {0: 'pre-catalog', 1: 'pre-catalog-v2', 2: 'pre-catalog-v3', 3: 'pre-catalog-v4'}.get(backup_target, 'pre-catalog')
        backup = _backup(database_path, backup_label) if existed and backup_target is not None else None
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
            if version < 3:
                opp_cols = {row[1] for row in conn.execute('PRAGMA table_info(opportunities)')}
                for column, definition in (
                    ('category', 'TEXT'),
                    ('lat', 'REAL'),
                    ('lng', 'REAL'),
                ):
                    if column not in opp_cols:
                        conn.execute(f'ALTER TABLE opportunities ADD COLUMN {column} {definition}')
            if version < 4:
                opp_cols = {row[1] for row in conn.execute('PRAGMA table_info(opportunities)')}
                for column, definition in (
                    ('discovery_source', 'TEXT'),
                    ('ats_provider', 'TEXT'),
                    ('role_bucket', 'TEXT'),
                    ('suburb_match_basis', 'TEXT'),
                ):
                    if column not in opp_cols:
                        conn.execute(f'ALTER TABLE opportunities ADD COLUMN {column} {definition}')
                ev_cols = {row[1] for row in conn.execute('PRAGMA table_info(opportunity_evidence)')}
                if 'check_outcome' not in ev_cols:
                    conn.execute('ALTER TABLE opportunity_evidence ADD COLUMN check_outcome TEXT')
                conn.execute(
                    "UPDATE opportunities SET discovery_source='OpenStreetMap discovery' "
                    "WHERE discovery_source IS NULL AND source='OpenStreetMap discovery'"
                )
                conn.execute(
                    "UPDATE opportunity_evidence SET check_outcome=TRIM(SUBSTR(excerpt, 24)) "
                    "WHERE check_outcome IS NULL AND excerpt LIKE 'Website check outcome:%'"
                )
                conn.execute(
                    "UPDATE opportunities SET description=NULL "
                    "WHERE verification_state='employer_lead' AND description LIKE 'Website check outcome:%'"
                )
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
            if version < 4:
                conn.execute('PRAGMA user_version=4')
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
