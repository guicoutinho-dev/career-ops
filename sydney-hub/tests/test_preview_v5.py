from __future__ import annotations

import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hub.storage.db import Database
from hub.storage.catalog import CatalogRepository
from hub.discovery.models import EmployerCandidate, SiteInspection, WebsiteFinding
from preview_v5 import copy_db_readonly, create_preview_app, validate_preview_port


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _create_v5_leads_db(db_path: Path) -> None:
    db = Database(db_path)
    catalog = CatalogRepository(db)
    for idx, suburb in enumerate(("Rockdale", "Mascot"), start=1):
        catalog.upsert_site_inspection(
            EmployerCandidate(
                website_url=f"https://employer{idx}.example.com.au/",
                discovery_source="OpenStreetMap discovery",
                suburb=suburb,
                category="cafe",
                source_key=f"node/{idx}",
                business_name=f"Employer {idx}",
                lat=-33.95 - idx * 0.01,
                lng=151.14 + idx * 0.01,
            ),
            SiteInspection(
                "ok",
                [
                    WebsiteFinding(
                        "employer_lead",
                        f"https://employer{idx}.example.com.au/",
                        f"Employer {idx}",
                        None,
                        None,
                        1700000000 + idx,
                    )
                ],
                1,
            ),
        )
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS published_source_keys ("
            "source_id TEXT NOT NULL, external_id TEXT NOT NULL, "
            "opportunity_id TEXT NOT NULL REFERENCES opportunities(id), "
            "PRIMARY KEY (source_id, external_id))"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS commute_observations ("
            "opportunity_id TEXT NOT NULL REFERENCES opportunities(id), "
            "origin_label TEXT NOT NULL, departure_window TEXT NOT NULL, "
            "departure_at TEXT NOT NULL, transit_minutes INTEGER NOT NULL, "
            "checked_at REAL NOT NULL, source_url TEXT NOT NULL, "
            "PRIMARY KEY (opportunity_id, origin_label, departure_window))"
        )
        conn.execute("PRAGMA user_version=5")
        conn.commit()


def _create_v2_db(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn:
        conn.executescript(
            """
            CREATE TABLE groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL UNIQUE, fb_group_id TEXT, name TEXT,
                enabled INTEGER NOT NULL DEFAULT 1, last_run_at REAL,
                last_status TEXT, last_new_posts INTEGER NOT NULL DEFAULT 0, created_at REAL
            );
            CREATE TABLE posts (
                post_id TEXT PRIMARY KEY, group_id INTEGER, group_url TEXT NOT NULL,
                group_name TEXT, permalink TEXT NOT NULL, author TEXT, author_url TEXT,
                body TEXT NOT NULL, created_ts REAL NOT NULL, photos TEXT NOT NULL DEFAULT '[]',
                comment_count INTEGER NOT NULL DEFAULT 0, reactions INTEGER NOT NULL DEFAULT 0,
                first_seen_at REAL NOT NULL, kind TEXT NOT NULL, classification TEXT NOT NULL,
                score INTEGER NOT NULL DEFAULT 0, ai_status TEXT NOT NULL DEFAULT 'pending',
                ai_json TEXT, is_new INTEGER NOT NULL DEFAULT 1, user_status TEXT NOT NULL DEFAULT '',
                legacy_ai_json TEXT, legacy_classification_json TEXT
            );
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, started_at REAL NOT NULL,
                finished_at REAL, trigger TEXT NOT NULL, status TEXT NOT NULL,
                groups_ok INTEGER NOT NULL DEFAULT 0, groups_failed INTEGER NOT NULL DEFAULT 0,
                new_posts INTEGER NOT NULL DEFAULT 0, error TEXT
            );
            CREATE TABLE employers (id TEXT PRIMARY KEY, name TEXT NOT NULL, website TEXT, location TEXT);
            CREATE TABLE opportunities (
                id TEXT PRIMARY KEY, employer_id TEXT REFERENCES employers(id),
                title TEXT, location TEXT, suburb TEXT, state TEXT,
                source TEXT NOT NULL, source_url TEXT NOT NULL,
                source_group_name TEXT, source_group_url TEXT,
                verification_state TEXT NOT NULL, first_seen_at REAL NOT NULL,
                published_at TEXT, last_verified_at TEXT, description TEXT,
                employment_type TEXT, experience_required TEXT, requirements TEXT,
                pay_display TEXT, pay_hourly_min REAL, visa_status TEXT NOT NULL DEFAULT 'unknown'
            );
            CREATE TABLE opportunity_evidence (
                id INTEGER PRIMARY KEY, opportunity_id TEXT NOT NULL, source_url TEXT NOT NULL,
                source_type TEXT NOT NULL, observed_at REAL NOT NULL, checked_at REAL,
                excerpt TEXT, supports_job INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE opportunity_commutes (
                opportunity_id TEXT NOT NULL, origin_suburb TEXT NOT NULL,
                transit_minutes INTEGER NOT NULL, checked_at REAL NOT NULL,
                source_url TEXT NOT NULL, PRIMARY KEY (opportunity_id, origin_suburb)
            );
            INSERT INTO employers(id, name) VALUES ('emp1', 'Main Cafe');
            INSERT INTO opportunities(
                id, employer_id, title, suburb, state, source, source_url, verification_state, first_seen_at
            ) VALUES (
                'opp1', 'emp1', 'Barista', 'Rockdale', 'NSW', 'Facebook Group',
                'https://facebook.com/groups/1/posts/1', 'unverified_job_post', 1700000000
            );
            PRAGMA user_version=2;
            """
        )


def test_copy_db_readonly_preserves_source_hash_and_writes_no_sidecars(tmp_path: Path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    src = src_dir / "hub.db"
    _create_v5_leads_db(src)

    before_sha = _sha256(src)
    before_mtime = src.stat().st_mtime_ns
    before_files = sorted(p.name for p in src_dir.iterdir())

    dest = tmp_path / "preview" / "hub_preview.db"
    result = copy_db_readonly(src, dest)

    assert _sha256(src) == before_sha
    assert src.stat().st_mtime_ns == before_mtime
    assert sorted(p.name for p in src_dir.iterdir()) == before_files
    assert not (src_dir / "hub.db-wal").exists()
    assert not (src_dir / "hub.db-shm").exists()

    assert result["source_sha256"] == before_sha
    assert Path(result["dest_db"]) == dest
    assert result["counts_by_state"] == {
        "verified_job": 0,
        "unverified_job_post": 0,
        "employer_lead": 2,
    }


def test_copy_db_readonly_safely_upgrades_v2_copy_without_touching_source(tmp_path: Path):
    src_dir = tmp_path / "v2src"
    src_dir.mkdir()
    src = src_dir / "hub.db"
    _create_v2_db(src)

    before_sha = _sha256(src)
    dest = tmp_path / "preview" / "hub_v5.db"
    result = copy_db_readonly(src, dest)

    assert _sha256(src) == before_sha
    with closing(sqlite3.connect(src)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        src_cols = {r[1] for r in conn.execute("PRAGMA table_info(opportunities)")}
        assert "role_bucket" not in src_cols

    with closing(sqlite3.connect(dest)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        dest_cols = {r[1] for r in conn.execute("PRAGMA table_info(opportunities)")}
        assert "verification_state" in dest_cols
        assert "role_bucket" in dest_cols

    assert result["counts_by_state"]["unverified_job_post"] == 1
    assert result["counts_by_state"]["verified_job"] == 0


def test_preview_app_disables_scheduler_and_blocks_manual_runs(tmp_path: Path):
    src = tmp_path / "src.db"
    _create_v5_leads_db(src)
    dest = tmp_path / "preview.db"
    copy_db_readonly(src, dest)

    web_dir = Path(__file__).resolve().parents[1] / "web"
    app = create_preview_app(dest, web_dir=web_dir, port=8795)
    client = TestClient(app, base_url="http://127.0.0.1:8795")

    summary = client.get("/api/opportunities/summary")
    assert summary.status_code == 200
    assert summary.json()["counts_by_state"] == {
        "verified_job": 0,
        "unverified_job_post": 0,
        "employer_lead": 2,
    }

    run_resp = client.post("/api/runs")
    assert run_resp.status_code in (400, 409)
    legacy_run_resp = client.post("/api/run")
    assert legacy_run_resp.status_code in (400, 409)

    status_resp = client.get("/api/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["state"] == "idle"

    opps = client.get("/api/opportunities")
    assert opps.status_code == 200
    items = opps.json()
    assert len(items) == 2
    assert all(item["verification_state"] == "employer_lead" for item in items)


def test_preview_app_rejects_non_localhost_host_header(tmp_path: Path):
    src = tmp_path / "src.db"
    _create_v5_leads_db(src)
    dest = tmp_path / "preview.db"
    copy_db_readonly(src, dest)

    web_dir = Path(__file__).resolve().parents[1] / "web"
    app = create_preview_app(dest, web_dir=web_dir, port=8795)
    client = TestClient(app, base_url="http://127.0.0.1:8795")

    resp = client.get("/api/opportunities/summary", headers={"Host": "evil.example:8795"})
    assert resp.status_code in (400, 403)

    with pytest.raises(ValueError):
        validate_preview_port("0.0.0.0", 8795)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        occupied_port = sock.getsockname()[1]
        with pytest.raises(RuntimeError):
            validate_preview_port("127.0.0.1", occupied_port)


def test_preview_cli_once_flag_exits_cleanly_on_temp_copy(tmp_path: Path):
    src = tmp_path / "src.db"
    _create_v5_leads_db(src)
    before_sha = _sha256(src)

    hub_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(hub_root / "preview_v5.py"), "--source-db", str(src), "--once"],
        cwd=hub_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["integrity"] == "ok"
    assert payload["counts_by_state"]["employer_lead"] == 2
    assert payload["opportunities_count"] == 2
    assert _sha256(src) == before_sha


def test_copy_db_readonly_and_keep_copy_reject_source_or_existing_dest(tmp_path: Path):
    src = tmp_path / "src.db"
    _create_v5_leads_db(src)
    before_sha = _sha256(src)
    before_mtime = src.stat().st_mtime_ns

    with pytest.raises(ValueError):
        copy_db_readonly(src, src)
    assert _sha256(src) == before_sha
    assert src.stat().st_mtime_ns == before_mtime

    existing = tmp_path / "existing.db"
    existing.write_bytes(b"preserve-existing-file")
    existing_sha = _sha256(existing)

    with pytest.raises(FileExistsError):
        copy_db_readonly(src, existing)
    assert _sha256(existing) == existing_sha

    hub_root = Path(__file__).resolve().parents[1]
    for bad_dest in (src, existing):
        proc = subprocess.run(
            [
                sys.executable,
                str(hub_root / "preview_v5.py"),
                "--source-db",
                str(src),
                "--keep-copy",
                str(bad_dest),
                "--once",
            ],
            cwd=hub_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert proc.returncode != 0
    assert _sha256(src) == before_sha
    assert _sha256(existing) == existing_sha


def test_preview_app_blocks_all_http_mutations_including_posts_without_breaking_get(tmp_path: Path):
    src = tmp_path / "src.db"
    _create_v5_leads_db(src)
    with closing(sqlite3.connect(src)) as conn:
        conn.execute(
            "INSERT INTO posts(post_id, group_url, permalink, body, created_ts, first_seen_at, kind, classification, is_new, user_status) "
            "VALUES ('post_1', 'https://www.facebook.com/groups/alpha/', 'https://www.facebook.com/groups/alpha/posts/1', 'Room available', 1700000000, 1700000000, 'stay', '{\"kind\":\"stay\",\"score\":0}', 1, '')"
        )
        conn.commit()

    dest = tmp_path / "preview.db"
    copy_db_readonly(src, dest)
    web_dir = Path(__file__).resolve().parents[1] / "web"
    app = create_preview_app(dest, web_dir=web_dir, port=8795)
    client = TestClient(app, base_url="http://127.0.0.1:8795")

    for path in ("/", "/api/posts", "/api/groups", "/api/status", "/api/opportunities", "/api/opportunities/summary"):
        assert client.get(path).status_code == 200

    assert client.post("/api/posts/seen").status_code == 409
    assert client.patch("/api/posts/post_1", json={"user_status": "saved"}).status_code == 409
    assert client.post("/api/groups", json={"url": "https://www.facebook.com/groups/beta/"}).status_code == 409
    assert client.post("/api/runs").status_code == 409
    assert client.post("/api/run").status_code == 409
    assert client.put("/api/posts/post_1").status_code == 409
    assert client.delete("/api/groups/1").status_code == 409

    posts = client.get("/api/posts").json()
    assert len(posts) == 1
    assert posts[0]["is_new"] is True
    assert posts[0]["user_status"] == ""
