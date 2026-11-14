from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import httpx
import pytest

import run_published_jobs as runner
from hub.discovery.published import ListingCheck, PublishedListing
from hub.discovery.published_ats import BoardAuditResult, OfficialBoard
from hub.storage.catalog import CatalogRepository
from hub.storage.db import Database, ISOLATED_DB_MARKER, ensure_published_schema


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_isolated_db(tmp_path: Path, name: str = "jobs_hub.db") -> tuple[Path, Path]:
    isolated_root = tmp_path / "jobs_isolated"
    isolated_root.mkdir(parents=True, exist_ok=True)
    (isolated_root / ISOLATED_DB_MARKER).write_text("isolated\n", encoding="utf-8")
    db_path = isolated_root / name
    ensure_published_schema(db_path, isolated_root=isolated_root)
    return db_path, isolated_root


def _write_policies(tmp_path: Path, sources_yaml: str) -> Path:
    path = tmp_path / "source_policies.yml"
    path.write_text(f"sources:\n{sources_yaml}", encoding="utf-8")
    return path


def listing(source: str = "workable", external_id: str = "1") -> PublishedListing:
    return PublishedListing(
        source,
        external_id,
        f"https://apply.workable.com/j/{external_id}",
        "Barista",
        "Test Cafe",
        "Rockdale NSW",
        datetime.now(timezone.utc),
        None,
        None,
        None,
        None,
    )


def test_workable_failure_does_not_block_official_ats(monkeypatch, tmp_path):
    seed = OfficialBoard(
        "Test Cafe",
        "https://job-boards.greenhouse.io/cafe",
        "https://example.com/careers",
        "2026-11-14",
        "greenhouse",
        discovery_origin="employer_careers_page",
        evidence_ref="https://example.com/careers",
        verified_at="2026-11-14T10:00:00+11:00",
    )
    policies_path = _write_policies(
        tmp_path,
        "  workable:\n"
        "    publish_allowed: true\n"
        "    attribution: Jobs by Workable\n"
        "    terms_url: https://help.workable.com/hc/en-us/articles/4420464031767-Utilizing-the-XML-Job-Feed\n"
        "    terms_reviewed_at: '2026-11-14'\n"
        "    access_method: public_xml_feed\n"
        "    retention_days: 30\n"
        "    cost_cap_aud: 0.0\n"
        "    max_requests: 10\n"
        "  ats:greenhouse:cafe:\n"
        "    publish_allowed: true\n"
        "    attribution: Official Employer Career Board (Greenhouse)\n"
        "    terms_url: https://example.com/careers\n"
        "    terms_reviewed_at: '2026-11-14'\n"
        "    access_method: public_board_api\n"
        "    retention_days: 30\n"
        "    cost_cap_aud: 0.0\n"
        "    max_requests: 25\n",
    )
    monkeypatch.setattr(runner, "fetch_workable", lambda _client: (_ for _ in ()).throw(ValueError("bad feed")))
    monkeypatch.setattr(runner, "load_official_boards", lambda _path: [seed])
    monkeypatch.setattr(runner, "validate_official_board", lambda *_args: True)
    monkeypatch.setattr(runner, "fetch_board_listings", lambda *_args, **_kwargs: [listing("ats:greenhouse:cafe")])
    monkeypatch.setattr(
        runner,
        "verify_job_page",
        lambda item, _client: ListingCheck("ok", datetime.now(timezone.utc).timestamp(), item.source_url, item.title),
    )
    db_path, isolated_root = _make_isolated_db(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(lambda _r: None)) as client:
        report = runner.run_published_jobs(
            db_path,
            tmp_path / "boards.yml",
            live=True,
            client=client,
            bridge=object(),
            policies_path=policies_path,
            state_dir=tmp_path / "state",
            reports_dir=tmp_path / "reports",
            isolated_root=isolated_root,
        )
    assert report["sources"]["workable"]["error"] == "bad feed"
    assert report["sources"]["ats:greenhouse:cafe"]["verified"] == 1
    assert CatalogRepository(Database(db_path)).count(verification_state="verified_job") == 1


def test_ats_failure_does_not_block_workable_and_repeat_run_keeps_id(monkeypatch, tmp_path):
    policies_path = _write_policies(
        tmp_path,
        "  workable:\n"
        "    publish_allowed: true\n"
        "    attribution: Jobs by Workable\n"
        "    terms_url: https://help.workable.com/hc/en-us/articles/4420464031767-Utilizing-the-XML-Job-Feed\n"
        "    terms_reviewed_at: '2026-11-14'\n"
        "    access_method: public_xml_feed\n"
        "    retention_days: 30\n"
        "    cost_cap_aud: 0.0\n"
        "    max_requests: 1\n"
        "  ats:lever:cafe:\n"
        "    publish_allowed: true\n"
        "    attribution: Official Employer Career Board (Lever)\n"
        "    terms_url: https://example.com/careers\n"
        "    terms_reviewed_at: '2026-11-14'\n"
        "    access_method: public_board_api\n"
        "    retention_days: 30\n"
        "    cost_cap_aud: 0.0\n"
        "    max_requests: 25\n",
    )
    monkeypatch.setattr(runner, "fetch_workable", lambda _client: [listing()])
    monkeypatch.setattr(
        runner,
        "load_official_boards",
        lambda _path: [
            OfficialBoard(
                "Cafe",
                "https://jobs.lever.co/cafe",
                "https://example.com/careers",
                "2026-11-14",
                "lever",
                discovery_origin="employer_careers_page",
                evidence_ref="https://example.com/careers",
                verified_at="2026-11-14T10:00:00+11:00",
            )
        ],
    )
    monkeypatch.setattr(runner, "validate_official_board", lambda *_args: True)
    monkeypatch.setattr(runner, "fetch_board_listings", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("429")))
    monkeypatch.setattr(
        runner,
        "verify_job_page",
        lambda item, _client: ListingCheck("ok", datetime.now(timezone.utc).timestamp(), item.source_url, item.title),
    )
    db_path, isolated_root = _make_isolated_db(tmp_path)
    with httpx.Client(transport=httpx.MockTransport(lambda _r: None)) as client:
        first = runner.run_published_jobs(
            db_path,
            tmp_path / "boards.yml",
            live=True,
            client=client,
            bridge=object(),
            policies_path=policies_path,
            state_dir=tmp_path / "state",
            reports_dir=tmp_path / "reports",
            isolated_root=isolated_root,
        )
        second = runner.run_published_jobs(
            db_path,
            tmp_path / "boards.yml",
            live=True,
            client=client,
            bridge=object(),
            policies_path=policies_path,
            state_dir=tmp_path / "state",
            reports_dir=tmp_path / "reports",
            isolated_root=isolated_root,
        )
    assert first["sources"]["workable"]["verified"] == second["sources"]["workable"]["verified"] == 1
    assert first["sources"]["ats:lever:cafe"]["error"] == "429"
    ids = [item["id"] for item in CatalogRepository(Database(db_path)).list()]
    assert len(ids) == 1 and ids[0].startswith("published_")


def test_dry_run_caps_verification_and_never_calls_adzuna_or_writes_db(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(runner, "fetch_workable", lambda _client: [listing(external_id=str(i)) for i in range(5)])
    monkeypatch.setattr(runner, "load_official_boards", lambda _path: [])
    monkeypatch.setattr(runner, "evaluate_adzuna_au", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Adzuna called")))
    monkeypatch.setattr(
        runner,
        "verify_job_page",
        lambda item, _client: (
            calls.append(item.source_url) or ListingCheck("ok", datetime.now(timezone.utc).timestamp(), item.source_url, item.title)
        ),
    )
    db_path = tmp_path / "jobs_isolated" / "hub_dry.db"
    with httpx.Client(transport=httpx.MockTransport(lambda _r: None)) as client:
        report = runner.run_published_jobs(
            db_path,
            tmp_path / "boards.yml",
            live=False,
            client=client,
            bridge=object(),
            max_verifications=2,
            state_dir=tmp_path / "state",
            reports_dir=tmp_path / "reports",
        )
    assert report["mode"] == "dry-run"
    assert report["sources"]["workable"]["verified"] == 2
    assert len(calls) == 2
    assert not db_path.exists()


def test_verify_job_page_rejects_closed_or_generic_pages_and_requires_specific_evidence(monkeypatch):
    monkeypatch.setattr(runner, "_public_url", lambda _url: True)
    item = listing()
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(
                200,
                text="<html><h1>Careers</h1><div>Barista</div><footer><a href='/apply'>Apply</a></footer></html>",
                headers={"content-type": "text/html"},
            )
        )
    ) as client:
        assert runner.verify_job_page(item, client).outcome == "unconfirmed"

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(
                200,
                text="<html><h1>Barista</h1><p>Applications closed</p><button>Apply</button></html>",
                headers={"content-type": "text/html"},
            )
        )
    ) as client:
        assert runner.verify_job_page(item, client).outcome == "closed_or_expired"

    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _r: httpx.Response(
                200,
                text="<html><h1>Barista</h1><button>Apply now</button></html>",
                headers={"content-type": "text/html"},
            )
        )
    ) as client:
        assert runner.verify_job_page(item, client).outcome == "ok"


def test_workable_canonical_redirect_and_v2_api_validation(monkeypatch):
    monkeypatch.setattr(runner, "_public_url", lambda _url: True)
    item = PublishedListing(
        "workable",
        "ABC123",
        "https://apply.workable.com/j/ABC123",
        "Waiter",
        "Teamified",
        "Windsor NSW",
        datetime.now(timezone.utc),
        None,
        None,
        None,
        None,
    )

    def ok_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/j/ABC123":
            return httpx.Response(301, headers={"location": "/teamified/j/ABC123"})
        if request.url.path == "/api/v2/accounts/teamified/jobs/ABC123":
            return httpx.Response(200, json={"shortcode": "ABC123", "title": "Waiter", "state": "published"})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(ok_handler)) as client:
        check = runner.verify_job_page(item, client)
    assert check.outcome == "ok"
    assert check.evidence_url == "https://apply.workable.com/teamified/j/ABC123"

    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(302, headers={"location": "/oops"}))) as client:
        assert runner.verify_job_page(item, client).outcome == "http_302"

    with httpx.Client(
        transport=httpx.MockTransport(lambda _r: httpx.Response(301, headers={"location": "https://evil.example/teamified/j/ABC123"}))
    ) as client:
        assert runner.verify_job_page(item, client).outcome == "http_301"

    def closed_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/j/ABC123":
            return httpx.Response(301, headers={"location": "/teamified/j/ABC123"})
        return httpx.Response(200, json={"shortcode": "ABC123", "title": "Waiter", "state": "closed"})

    with httpx.Client(transport=httpx.MockTransport(closed_handler)) as client:
        assert runner.verify_job_page(item, client).outcome == "closed_or_expired"


def test_live_run_on_unapproved_source_or_unapproved_ats_board_performs_zero_catalog_writes(monkeypatch, tmp_path):
    db_path, isolated_root = _make_isolated_db(tmp_path)
    policies_path = _write_policies(
        tmp_path,
        "  workable:\n"
        "    publish_allowed: false\n"
        "    attribution: Jobs by Workable\n"
        "    cost_cap_aud: 0.0\n"
        "  ats:greenhouse:\n"
        "    publish_allowed: true\n"
        "    attribution: Greenhouse Wildcard Should Not Inherit\n"
        "    terms_url: https://www.greenhouse.io/legal\n"
        "    terms_reviewed_at: '2026-11-14'\n"
        "    access_method: public_board_api\n"
        "    retention_days: 30\n"
        "    cost_cap_aud: 0.0\n"
        "    max_requests: 25\n",
    )
    unapproved_board = OfficialBoard(
        "Unapproved Bar",
        "https://job-boards.greenhouse.io/unapproved-bar",
        "https://bar.com.au/careers",
        "2026-11-14",
        "greenhouse",
        discovery_origin="employer_careers_page",
        evidence_ref="https://bar.com.au/careers",
        verified_at="2026-11-14T10:00:00+11:00",
    )
    monkeypatch.setattr(runner, "fetch_workable", lambda _c: [listing("workable", "W1")])
    monkeypatch.setattr(runner, "load_official_boards", lambda _p: [unapproved_board])
    monkeypatch.setattr(runner, "validate_official_board", lambda *_a: True)
    monkeypatch.setattr(
        runner,
        "fetch_board_listings",
        lambda *_a, **_k: [listing("ats:greenhouse:unapproved-bar", "G1")],
    )
    monkeypatch.setattr(
        runner,
        "verify_job_page",
        lambda item, _c: ListingCheck("ok", datetime.now(timezone.utc).timestamp(), item.source_url, item.title),
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _r: None)) as client:
        report = runner.run_published_jobs(
            db_path,
            tmp_path / "boards.yml",
            live=True,
            client=client,
            bridge=object(),
            policies_path=policies_path,
            state_dir=tmp_path / "state",
            reports_dir=tmp_path / "reports",
            isolated_root=isolated_root,
        )
    assert report["sources"]["workable"]["last_outcome"] == "skipped_unapproved"
    assert report["sources"]["ats:greenhouse:unapproved-bar"]["last_outcome"] == "skipped_unapproved"
    assert CatalogRepository(Database(db_path)).count() == 0


def test_live_run_rejects_v4_or_outside_db_before_schema_or_snapshot(tmp_path):
    outside = tmp_path / "outside.db"
    Database(outside)
    sha_before = _sha256(outside)
    mtime_before = outside.stat().st_mtime_ns
    isolated_root = tmp_path / "jobs_isolated"
    isolated_root.mkdir()
    (isolated_root / ISOLATED_DB_MARKER).write_text("isolated\n", encoding="utf-8")

    with httpx.Client(transport=httpx.MockTransport(lambda _r: None)) as client:
        with pytest.raises((ValueError, PermissionError)):
            runner.run_published_jobs(
                outside,
                tmp_path / "boards.yml",
                live=True,
                client=client,
                bridge=object(),
                policies_path=tmp_path / "policies.yml",
                state_dir=tmp_path / "state",
                reports_dir=tmp_path / "reports",
                isolated_root=isolated_root,
            )
    assert _sha256(outside) == sha_before
    assert outside.stat().st_mtime_ns == mtime_before


def test_workable_cooldown_persists_across_runs_without_bypass(monkeypatch, tmp_path):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(429, headers={"retry-after": "1800"}, request=request)

    db_path, isolated_root = _make_isolated_db(tmp_path)
    policies_path = _write_policies(
        tmp_path,
        "  workable:\n"
        "    publish_allowed: true\n"
        "    attribution: Jobs by Workable\n"
        "    terms_url: https://help.workable.com/hc/en-us/articles/4420464031767-Utilizing-the-XML-Job-Feed\n"
        "    terms_reviewed_at: '2026-11-14'\n"
        "    access_method: public_xml_feed\n"
        "    retention_days: 30\n"
        "    cost_cap_aud: 0.0\n"
        "    max_requests: 1\n"
        "  ats:greenhouse:cafe:\n"
        "    publish_allowed: true\n"
        "    attribution: Official Employer Career Board (Greenhouse)\n"
        "    terms_url: https://cafe.com.au/careers\n"
        "    terms_reviewed_at: '2026-11-14'\n"
        "    access_method: public_board_api\n"
        "    retention_days: 30\n"
        "    cost_cap_aud: 0.0\n"
        "    max_requests: 25\n",
    )
    seed = OfficialBoard(
        "Cafe",
        "https://job-boards.greenhouse.io/cafe",
        "https://cafe.com.au/careers",
        "2026-11-14",
        "greenhouse",
        discovery_origin="employer_careers_page",
        evidence_ref="https://cafe.com.au/careers",
        verified_at="2026-11-14T10:00:00+11:00",
    )
    monkeypatch.setattr(runner, "load_official_boards", lambda _p: [seed])
    monkeypatch.setattr(runner, "validate_official_board", lambda *_a: True)
    monkeypatch.setattr(
        runner,
        "fetch_board_listings",
        lambda *_a, **_k: [listing("ats:greenhouse:cafe", "G1")],
    )
    monkeypatch.setattr(
        runner,
        "verify_job_page",
        lambda item, _c: ListingCheck("ok", datetime.now(timezone.utc).timestamp(), item.source_url, item.title),
    )

    state_dir = tmp_path / "state"
    reports_dir = tmp_path / "reports"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = runner.run_published_jobs(
            db_path,
            tmp_path / "boards.yml",
            live=True,
            client=client,
            bridge=object(),
            policies_path=policies_path,
            state_dir=state_dir,
            reports_dir=reports_dir,
            isolated_root=isolated_root,
        )
        second = runner.run_published_jobs(
            db_path,
            tmp_path / "boards.yml",
            live=True,
            client=client,
            bridge=object(),
            policies_path=policies_path,
            state_dir=state_dir,
            reports_dir=reports_dir,
            isolated_root=isolated_root,
        )
    assert len(requests) == 1
    assert first["sources"]["workable"]["error"] == "http_429_retry_after_1800"
    assert second["sources"]["workable"]["last_outcome"] == "cooldown"
    assert second["sources"]["ats:greenhouse:cafe"]["verified"] == 1


def test_runner_caches_origin_stop_and_persists_canonical_workable_url(monkeypatch, tmp_path):
    from zoneinfo import ZoneInfo
    from hub.discovery.transit import CommuteResult

    origin_calls, commute_calls = [], []

    def exact(ext_id: str) -> PublishedListing:
        return PublishedListing(
            "workable",
            ext_id,
            f"https://apply.workable.com/j/{ext_id}",
            "Barista",
            "Cafe",
            "Rockdale NSW",
            datetime.now(timezone.utc),
            "Casual",
            None,
            -33.954,
            151.14,
        )

    policies_path = _write_policies(
        tmp_path,
        "  workable:\n"
        "    publish_allowed: true\n"
        "    attribution: Jobs by Workable\n"
        "    terms_url: https://help.workable.com/hc/en-us/articles/4420464031767-Utilizing-the-XML-Job-Feed\n"
        "    terms_reviewed_at: '2026-11-14'\n"
        "    access_method: public_xml_feed\n"
        "    retention_days: 30\n"
        "    cost_cap_aud: 0.0\n"
        "    max_requests: 10\n",
    )
    monkeypatch.setenv("TFNSW_API_KEY", "test-key")
    monkeypatch.setenv("TFNSW_FREE_QUOTA_CONFIRMED", "1")
    monkeypatch.setattr(runner, "fetch_workable", lambda _c: [exact("W1"), exact("W1"), exact("W2")])
    monkeypatch.setattr(runner, "load_official_boards", lambda _p: [])
    monkeypatch.setattr(
        runner,
        "verify_job_page",
        lambda item, _c: ListingCheck(
            "ok",
            datetime.now(timezone.utc).timestamp(),
            f"https://apply.workable.com/cafe/j/{item.external_id}",
            item.title,
        ),
    )
    monkeypatch.setattr(runner, "resolve_origin_stop", lambda _c, api_key: (origin_calls.append(api_key) or "stop-rockdale"))
    monkeypatch.setattr(
        runner,
        "reference_departures",
        lambda _d: [("tuesday_0900", datetime(2026, 11, 17, 9, 0, tzinfo=ZoneInfo("Australia/Sydney")))],
    )
    monkeypatch.setattr(
        runner,
        "measure_commute",
        lambda *a, **k: (
            commute_calls.append(a[1])
            or CommuteResult(14, "https://api.transport.nsw.gov.au/v1/tp/trip", datetime.now(timezone.utc).timestamp(), "ok")
        ),
    )
    db_path, isolated_root = _make_isolated_db(tmp_path)
    reports_dir = tmp_path / "reports"
    with httpx.Client(transport=httpx.MockTransport(lambda _r: None)) as client:
        report = runner.run_published_jobs(
            db_path,
            tmp_path / "boards.yml",
            live=True,
            client=client,
            bridge=object(),
            policies_path=policies_path,
            state_dir=tmp_path / "state",
            reports_dir=reports_dir,
            isolated_root=isolated_root,
        )
    assert len(origin_calls) == 1
    assert len(commute_calls) == 2
    assert report["transit"]["measured"] == 2
    assert Path(str(db_path) + ".published-run-rollback.json").is_file()

    rows = CatalogRepository(Database(db_path)).list()
    assert {r["source_url"] for r in rows} == {
        "https://apply.workable.com/j/W1",
        "https://apply.workable.com/j/W2",
    }
    evidence_urls = {
        e["source_url"]
        for r in rows
        for e in r["evidence"]
        if e["source_type"] == "source_feed"
    }
    assert evidence_urls == {
        "https://apply.workable.com/cafe/j/W1",
        "https://apply.workable.com/cafe/j/W2",
    }
    journal_lines = [json.loads(line) for line in (reports_dir / "yield_journal.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(journal_lines) == 1
    assert journal_lines[0]["cost_aud"] == 0.0
    assert journal_lines[0]["duplicate_within_source"] == 1
    assert "collisions" in journal_lines[0]


def test_transient_workable_fetch_retries_once_without_blocking(monkeypatch, tmp_path):
    attempts = []

    def fetch(_client):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("temporary")
        return [listing()]

    monkeypatch.setattr(runner, "fetch_workable", fetch)
    monkeypatch.setattr(runner, "load_official_boards", lambda _path: [])
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        runner,
        "verify_job_page",
        lambda item, _client: ListingCheck("ok", datetime.now(timezone.utc).timestamp(), item.source_url, item.title),
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _r: None)) as client:
        report = runner.run_published_jobs(
            tmp_path / "jobs_isolated" / "hub.db",
            tmp_path / "boards.yml",
            client=client,
            bridge=object(),
            state_dir=tmp_path / "state",
            reports_dir=tmp_path / "reports",
        )
    assert len(attempts) == 2
    assert report["sources"]["workable"]["verified"] == 1
