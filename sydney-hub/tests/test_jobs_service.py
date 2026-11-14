from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import httpx
from fastapi.testclient import TestClient

import jobs_app
import run_published_jobs as runner
from hub.discovery.published import ListingCheck, PublishedListing
from hub.settings import JobsConfig, PublishedJobsSettings, ServerSettings
from hub.storage.db import ISOLATED_DB_MARKER, ensure_published_schema


def _make_cfg(tmp_path: Path) -> JobsConfig:
    isolated_root = tmp_path / "data" / "jobs_isolated"
    isolated_root.mkdir(parents=True, exist_ok=True)
    (isolated_root / ISOLATED_DB_MARKER).write_text("isolated\n", encoding="utf-8")
    db_path = isolated_root / "jobs_hub.db"
    ensure_published_schema(db_path, isolated_root=isolated_root)

    web_dir = tmp_path / "web"
    web_dir.mkdir(exist_ok=True)
    (web_dir / "index.html").write_text("<html>SydneyStays Jobs</html>", encoding="utf-8")

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    (cfg_dir / "published_boards.yml").write_text("boards: []\n", encoding="utf-8")
    (cfg_dir / "source_policies.yml").write_text(
        "sources:\n"
        "  workable:\n"
        "    publish_allowed: true\n"
        "    attribution: Jobs by Workable\n"
        "    terms_url: https://help.workable.com/hc/en-us/articles/4420464031767-Utilizing-the-XML-Job-Feed\n"
        "    terms_reviewed_at: '2026-11-14'\n"
        "    access_method: public_xml_feed\n"
        "    retention_days: 30\n"
        "    cost_cap_aud: 0.0\n"
        "    max_requests: 10\n",
        encoding="utf-8",
    )
    return JobsConfig(
        server=ServerSettings(host="127.0.0.1", port=8796),
        published_jobs=PublishedJobsSettings(
            db_path="data/jobs_isolated/jobs_hub.db",
            isolated_db_dir="data/jobs_isolated",
            policies_path="config/source_policies.yml",
            boards_path="config/published_boards.yml",
            state_dir="data/published-state",
            reports_dir="data/pilot-reports",
            scheduler_enabled=False,
        ),
        root=tmp_path,
    )


def test_jobs_only_service_disables_facebook_and_blocks_fb_mutations(monkeypatch, tmp_path):
    import hub.adapters.process as proc_mod

    monkeypatch.setattr(proc_mod, "resolve_maestri", lambda: (_ for _ in ()).throw(AssertionError("Maestri called")))
    cfg = _make_cfg(tmp_path)
    cfg.paths.web = "web"

    app, scheduler = jobs_app.build_jobs_service(cfg, start_scheduler=False)
    try:
        client = TestClient(app, base_url="http://127.0.0.1:8796")
        assert client.get("/api/opportunities").status_code == 200
        health = client.get("/api/jobs/health")
        assert health.status_code == 200
        assert health.json()["mode"] == "jobs_only"
        assert health.json()["scheduler_active"] is False

        assert client.get("/api/opportunities", headers={"Host": "evil.example:8796"}).status_code == 403
        for method, path, body in (
            ("POST", "/api/runs", None),
            ("POST", "/api/groups", {"url": "https://www.facebook.com/groups/alpha/"}),
            ("PATCH", "/api/groups/1", {"enabled": False}),
            ("DELETE", "/api/groups/1", None),
            ("POST", "/api/posts/seen", None),
            ("PATCH", "/api/posts/p1", {"user_status": "fav"}),
        ):
            r = client.request(method, path, json=body)
            assert r.status_code == 409, f"{method} {path} returned {r.status_code}"
    finally:
        scheduler.shutdown()


def test_jobs_only_manual_run_endpoint_and_cli_once(tmp_path):
    cfg = _make_cfg(tmp_path)
    cfg.paths.web = "web"
    cfg.published_jobs.db_path = "data/jobs_isolated/jobs_hub.db"
    cfg_file = tmp_path / "config" / "jobs.yml"
    cfg_file.write_text(
        "server:\n  host: 127.0.0.1\n  port: 8796\n"
        "paths:\n  db: data/jobs_isolated/jobs_hub.db\n  web: web\n"
        "published_jobs:\n"
        "  db_path: data/jobs_isolated/jobs_hub.db\n"
        "  isolated_db_dir: data/jobs_isolated\n"
        "  policies_path: config/source_policies.yml\n"
        "  boards_path: config/published_boards.yml\n"
        "  state_dir: data/published-state\n"
        "  reports_dir: data/pilot-reports\n",
        encoding="utf-8",
    )

    app, scheduler = jobs_app.build_jobs_service(cfg, start_scheduler=False, spawn=lambda fn: None, jobs_running_override=lambda: True)
    try:
        client = TestClient(app, base_url="http://127.0.0.1:8796")
        assert client.post("/api/jobs/runs").status_code == 409
    finally:
        scheduler.shutdown()

    calls = []
    app2, scheduler2 = jobs_app.build_jobs_service(
        cfg,
        start_scheduler=False,
        run_fn=lambda *, skip_workable=False: calls.append(skip_workable) or {"ok": True},
        spawn=lambda fn: fn(),
    )
    try:
        client2 = TestClient(app2, base_url="http://127.0.0.1:8796")
        r = client2.post("/api/jobs/runs")
        assert r.status_code == 202 and calls == [False]
    finally:
        scheduler2.shutdown()

    proc = subprocess.run(
        [sys.executable, "jobs_app.py", "--config", str(cfg_file), "--once"],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["mode"] == "jobs_only"
    assert payload["scheduler_active"] is False


def test_e2e_runner_to_isolated_db_to_api_and_ui_with_fixture_verified_job(monkeypatch, tmp_path):
    from shutil import copytree

    cfg = _make_cfg(tmp_path)
    real_web = Path(__file__).resolve().parent.parent / "web"
    copytree(real_web, tmp_path / "web", dirs_exist_ok=True)
    cfg.paths.web = "web"

    item = PublishedListing(
        "workable",
        "WK99",
        "https://apply.workable.com/j/WK99",
        "Morning Barista",
        "Harbour Roasters",
        "452 Princes Hwy, Rockdale NSW 2216",
        datetime.now(timezone.utc),
        "Casual",
        "Specialty espresso venue",
        None,
        None,
    )
    monkeypatch.setattr(runner, "fetch_workable", lambda _c: [item])
    monkeypatch.setattr(runner, "load_official_boards", lambda _p: [])
    monkeypatch.setattr(
        runner,
        "verify_job_page",
        lambda listing_item, _c: ListingCheck(
            "ok",
            datetime.now(timezone.utc).timestamp(),
            "https://apply.workable.com/harbour/j/WK99",
            listing_item.title,
        ),
    )

    with httpx.Client(transport=httpx.MockTransport(lambda _r: None)) as client:
        runner.run_published_jobs(
            cfg.resolve(cfg.published_jobs.db_path),
            cfg.resolve(cfg.published_jobs.boards_path),
            live=True,
            client=client,
            bridge=object(),
            policies_path=cfg.resolve(cfg.published_jobs.policies_path),
            state_dir=cfg.resolve(cfg.published_jobs.state_dir),
            reports_dir=cfg.resolve(cfg.published_jobs.reports_dir),
            isolated_root=cfg.resolve(cfg.published_jobs.isolated_db_dir),
        )

    app, scheduler = jobs_app.build_jobs_service(cfg, start_scheduler=False)
    try:
        http = TestClient(app, base_url="http://127.0.0.1:8796")
        opps = http.get("/api/opportunities?verification_state=verified_job").json()
        assert len(opps) == 1
        assert opps[0]["title"] == "Morning Barista"
        assert opps[0]["suburb"] == "Rockdale"
        assert opps[0]["source_url"] == "https://apply.workable.com/j/WK99"
        assert any(e["source_url"] == "https://apply.workable.com/harbour/j/WK99" for e in opps[0]["evidence"])
        html = http.get("/").text
        assert "Olympian" in html or "SydneyStays" in html
    finally:
        scheduler.shutdown()
