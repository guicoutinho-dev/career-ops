import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from hub.storage.db import (
    Database,
    ensure_published_schema,
    migrate_published_catalog,
    restore_published_run,
    snapshot_published_run,
)
from hub.storage.jobs_bootstrap import (
    ISOLATED_DB_MARKER,
    bootstrap_isolated_jobs_db,
    validate_isolated_jobs_db_path,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_v4_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Database(path)
    with Database(path).connect() as conn:
        conn.execute(
            "INSERT INTO opportunities (id, title, source, source_url, verification_state, first_seen_at) "
            "VALUES ('opp-1', 'Barista', 'osm', 'https://cafe.example.com', 'employer_lead', 1000.0)"
        )


def test_rejects_v4_main_and_outside_db_paths_before_schema_or_snapshot_leaving_hash_and_mtime_untouched(tmp_path):
    isolated_root = tmp_path / "data" / "jobs_isolated"
    isolated_root.mkdir(parents=True)
    (isolated_root / ISOLATED_DB_MARKER).write_text("isolated\n", encoding="utf-8")

    outside_db = tmp_path / "outside.db"
    _make_v4_db(outside_db)
    v4_like_db = tmp_path / "career-ops" / "sydney-hub" / "data" / "hub.db"
    _make_v4_db(v4_like_db)
    main_like_db = tmp_path / "sydney-hub" / "data" / "hub.db"
    _make_v4_db(main_like_db)

    baselines = {
        p: (_sha256(p), p.stat().st_mtime_ns)
        for p in (outside_db, v4_like_db, main_like_db)
    }

    unmarked_root = tmp_path / "data" / "unmarked_isolated"
    unmarked_root.mkdir(parents=True)
    unmarked_db = unmarked_root / "jobs.db"
    _make_v4_db(unmarked_db)
    baselines[unmarked_db] = (_sha256(unmarked_db), unmarked_db.stat().st_mtime_ns)

    # Attempt symlink escape if supported by OS/privileges.
    symlink_escape = isolated_root / "escape.db"
    try:
        symlink_escape.symlink_to(outside_db)
    except (OSError, NotImplementedError):
        symlink_escape = None

    traversal = isolated_root / ".." / "outside.db"
    forbidden_targets = [
        (outside_db, isolated_root),
        (v4_like_db, isolated_root),
        (main_like_db, isolated_root),
        (traversal, isolated_root),
        (unmarked_db, unmarked_root),
        (Path(r"C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops\sydney-hub\data\hub.db"), isolated_root),
        (Path(r"D:\AntigravityProjetos\career-ops\sydney-hub\data\hub.db"), isolated_root),
    ]
    if symlink_escape is not None:
        forbidden_targets.append((symlink_escape, isolated_root))

    for target, root in forbidden_targets:
        with pytest.raises((ValueError, PermissionError)):
            validate_isolated_jobs_db_path(target, isolated_root=root)
        with pytest.raises((ValueError, PermissionError)):
            ensure_published_schema(target, isolated_root=root)
        with pytest.raises((ValueError, PermissionError)):
            snapshot_published_run(target, isolated_root=root)

    # Bootstrapping into outside_db / v4 / main must fail BEFORE opening or modifying anything.
    fresh_outside = tmp_path / "fresh_outside.db"
    with pytest.raises((ValueError, PermissionError)):
        bootstrap_isolated_jobs_db(outside_db, fresh_outside, isolated_root=isolated_root)
    assert not fresh_outside.exists()

    for path, (sha_before, mtime_before) in baselines.items():
        assert _sha256(path) == sha_before
        assert path.stat().st_mtime_ns == mtime_before
        assert not Path(str(path) + ".published-run-rollback.json").exists()
        assert not Path(str(path) + ".published-v5-rollback.json").exists()
        assert not Path(str(path) + "-wal").exists()
        assert not Path(str(path) + "-shm").exists()


def test_bootstrap_isolated_jobs_db_preserves_source_sha_refuses_existing_and_handles_v5(tmp_path):
    src_v5 = tmp_path / "v4_pilot_copy.db"
    _make_v4_db(src_v5)
    migrate_published_catalog(src_v5)
    # Remove v5 rollback manifest to mimic external V4 copy lacking local manifest.
    Path(str(src_v5) + ".published-v5-rollback.json").unlink()
    src_sha = _sha256(src_v5)
    src_mtime = src_v5.stat().st_mtime_ns

    isolated_root = tmp_path / "jobs_isolated"
    dest = isolated_root / "jobs_hub.db"
    summary = bootstrap_isolated_jobs_db(src_v5, dest, isolated_root=isolated_root)

    assert _sha256(src_v5) == src_sha
    assert src_v5.stat().st_mtime_ns == src_mtime
    assert not Path(str(src_v5) + "-wal").exists()
    assert not Path(str(src_v5) + "-shm").exists()
    assert (isolated_root / ISOLATED_DB_MARKER).is_file()
    assert summary["integrity"] == "ok"
    assert ensure_published_schema(dest, isolated_root=isolated_root) == 5

    with sqlite3.connect(dest) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1

    with pytest.raises(ValueError):
        bootstrap_isolated_jobs_db(src_v5, src_v5, isolated_root=src_v5.parent)
    with pytest.raises(FileExistsError):
        bootstrap_isolated_jobs_db(src_v5, dest, isolated_root=isolated_root)


def test_pre_run_snapshot_and_rollback_restores_exact_v5_state(tmp_path):
    isolated_root = tmp_path / "jobs_isolated"
    dest = isolated_root / "jobs_hub.db"
    src_v4 = tmp_path / "src.db"
    _make_v4_db(src_v4)
    bootstrap_isolated_jobs_db(src_v4, dest, isolated_root=isolated_root)

    backup = snapshot_published_run(dest, isolated_root=isolated_root)
    assert backup.is_file()

    with Database(dest).connect() as conn:
        conn.execute(
            "INSERT INTO opportunities (id, title, source, source_url, verification_state, first_seen_at) "
            "VALUES ('opp-2', 'Kitchen Hand', 'workable', 'https://apply.workable.com/j/K1', 'verified_job', 2000.0)"
        )

    preserved = restore_published_run(dest, isolated_root=isolated_root, force=True)
    assert preserved is not None and preserved.is_file()
    with sqlite3.connect(dest) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        ids = [r[0] for r in conn.execute("SELECT id FROM opportunities ORDER BY id").fetchall()]
        assert ids == ["opp-1"]


def test_rollback_catalog_cli_pre_run_rejects_outside_db_without_altering_mtime(tmp_path):
    import subprocess
    import sys

    outside = tmp_path / "outside.db"
    _make_v4_db(outside)
    sha_before = _sha256(outside)
    mtime_before = outside.stat().st_mtime_ns
    isolated_root = tmp_path / "jobs_isolated"
    isolated_root.mkdir()
    (isolated_root / ISOLATED_DB_MARKER).write_text("isolated\n", encoding="utf-8")

    proc = subprocess.run(
        [
            sys.executable,
            "rollback_catalog.py",
            "--database",
            str(outside),
            "--isolated-root",
            str(isolated_root),
            "--pre-run",
            "--force",
        ],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert _sha256(outside) == sha_before
    assert outside.stat().st_mtime_ns == mtime_before


def test_bootstrap_none_refuses_main_and_v4_roots_without_creating_marker_or_db(tmp_path):
    # Reproduces both temp replicas of V4/main data dirs AND real protected V4/main roots
    main_like = tmp_path / "career-ops" / "sydney-hub" / "data"
    main_like.mkdir(parents=True)
    main_hub = main_like / "hub.db"
    _make_v4_db(main_hub)
    main_sha, main_mtime = _sha256(main_hub), main_hub.stat().st_mtime_ns

    v4_like = tmp_path / "worktrees" / "sydneystays-employer-pilot" / "career-ops" / "sydney-hub" / "data"
    v4_like.mkdir(parents=True)

    for bad_root in (
        main_like,
        v4_like,
        Path(r"D:\AntigravityProjetos\career-ops\sydney-hub\data"),
        Path(r"C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops\sydney-hub\data"),
    ):
        with pytest.raises((ValueError, PermissionError)):
            bootstrap_isolated_jobs_db(None, bad_root / "jobs_hub.db", isolated_root=bad_root)

    assert not (main_like / ISOLATED_DB_MARKER).exists()
    assert not (main_like / "jobs_hub.db").exists()
    assert not (v4_like / ISOLATED_DB_MARKER).exists()
    assert not (v4_like / "jobs_hub.db").exists()
    assert _sha256(main_hub) == main_sha
    assert main_hub.stat().st_mtime_ns == main_mtime
