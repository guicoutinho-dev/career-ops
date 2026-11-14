from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from hub.api.app import AppDeps, create_app
from hub.discovery.published import ListingCheck, PublishedListing, SourcePolicy
from hub.domain.models import Run, Trigger
from hub.pipeline.routine import RunAlreadyActive
from hub.storage.catalog import CatalogRepository
from hub.storage.db import Database, migrate_lifecycle_catalog, migrate_published_catalog
from hub.storage.groups import GroupRepository
from hub.storage.posts import PostRepository
from hub.storage.runs import RunRepository


HOST = "127.0.0.1:8765"


class _FakeRunner:
    @property
    def is_running(self) -> bool:
        return False

    def run(self, trigger: Trigger) -> Run:
        raise RunAlreadyActive()


class _FakeScheduler:
    def next_run_at(self):
        return None


def _make_client(tmp_path, *, candidate_root=None):
    db_path = tmp_path / "hub.db"
    db = Database(db_path)
    migrate_published_catalog(db_path)
    migrate_lifecycle_catalog(db_path)
    web = tmp_path / "web"
    web.mkdir(exist_ok=True)
    (web / "index.html").write_text("<html>ok</html>", encoding="utf-8")
    catalog = CatalogRepository(db)
    deps = AppDeps(
        groups=GroupRepository(db),
        posts=PostRepository(db),
        runs=RunRepository(db),
        catalog=catalog,
        runner=_FakeRunner(),
        scheduler=_FakeScheduler(),
        web_dir=web,
        candidate_root=candidate_root,
        allowed_hosts={HOST, "localhost:8765"},
        spawn=lambda fn: fn(),
    )
    return TestClient(create_app(deps), base_url=f"http://{HOST}"), catalog


def test_opportunities_include_matching_462_88days_and_lifecycle_counts(tmp_path):
    client, catalog = _make_client(tmp_path)
    now = datetime.now(timezone.utc)

    syd_barista = PublishedListing(
        "workable", "S1", "https://apply.workable.com/cafe/j/S1/",
        "Barista (No experience required)", "Rockdale Cafe", "Rockdale NSW 2216",
        now - timedelta(hours=5), "Casual", "$29/h", None, "WHV 462 welcome; RSA",
    )
    cairns_hospo = PublishedListing(
        "workable", "N1", "https://apply.workable.com/reef/j/N1/",
        "Barista & Waitstaff", "Reef Hotel", "Cairns QLD 4870",
        now - timedelta(hours=6), "Casual", "$30/h", None, "462 specified work friendly",
    )
    expired_cleaner = PublishedListing(
        "workable", "E1", "https://apply.workable.com/clean/j/E1/",
        "Office Cleaner", "CleanCo", "Mascot NSW 2020",
        now - timedelta(days=3), "Part-time", "$28/h", None, None,
    )

    catalog.upsert_published_listing(
        syd_barista,
        ListingCheck("ok", now.timestamp(), syd_barista.source_url, syd_barista.title),
        SourcePolicy("workable", True, "Jobs by Workable"),
        role_bucket="barista",
    )
    catalog.upsert_published_listing(
        cairns_hospo,
        ListingCheck("ok", now.timestamp(), cairns_hospo.source_url, cairns_hospo.title),
        SourcePolicy("workable", True, "Jobs by Workable"),
        role_bucket="barista",
    )
    exp_id = catalog.upsert_published_listing(
        expired_cleaner,
        ListingCheck("ok", now.timestamp(), expired_cleaner.source_url, expired_cleaner.title),
        SourcePolicy("workable", True, "Jobs by Workable"),
        role_bucket="cleaner",
    )
    catalog.apply_listing_check_lifecycle(
        exp_id,
        ListingCheck("http_404", now.timestamp() + 10, expired_cleaner.source_url, None),
    )

    resp = client.get("/api/opportunities")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 3
    by_title = {item["title"]: item for item in items}

    assert "match" in by_title["Barista (No experience required)"]
    assert by_title["Barista (No experience required)"]["match"]["score"] > 0
    assert by_title["Barista (No experience required)"]["specified_work_462"]["status"] == "ineligible"
    assert by_title["Barista & Waitstaff"]["specified_work_462"]["status"] == "eligible"
    assert by_title["Office Cleaner"]["lifecycle_state"] == "expired"

    # Separate 88-days view returns non-ineligible 462 opportunities (or filters by status)
    r88 = client.get("/api/opportunities?specified_work_status=eligible")
    assert r88.status_code == 200
    assert [it["title"] for it in r88.json()] == ["Barista & Waitstaff"]

    r_active = client.get("/api/opportunities?include_expired=false")
    assert r_active.status_code == 200
    assert {it["title"] for it in r_active.json()} == {
        "Barista (No experience required)",
        "Barista & Waitstaff",
    }

    summary = client.get("/api/opportunities/summary").json()
    assert summary["specified_work_counts"]["eligible"] == 1
    assert summary["specified_work_counts"]["ineligible"] >= 1
    assert summary["lifecycle_counts"]["expired"] == 1
    assert summary["lifecycle_counts"]["active"] == 2


def test_prepare_application_endpoint_returns_source_grounded_packet_without_autosubmit(tmp_path):
    cand_root = tmp_path / "cand"
    (cand_root / "config").mkdir(parents=True)
    (cand_root / "config" / "profile.yml").write_text(
        "candidate:\n  name: Gui\n  visa: WHV 462\n  tickets:\n    - RSA\n",
        encoding="utf-8",
    )
    client, catalog = _make_client(tmp_path, candidate_root=cand_root)
    now = datetime.now(timezone.utc)
    listing = PublishedListing(
        "workable", "P1", "https://apply.workable.com/cafe/j/P1/",
        "Barista", "Bay Cafe", "Rockdale NSW 2216",
        now - timedelta(hours=2), "Casual", "$29/h", None, "RSA",
    )
    opp_id = catalog.upsert_published_listing(
        listing,
        ListingCheck("ok", now.timestamp(), listing.source_url, listing.title),
        SourcePolicy("workable", True, "Jobs by Workable"),
        role_bucket="barista",
    )

    res = client.post(f"/api/opportunities/{opp_id}/prepare")
    assert res.status_code == 200
    body = res.json()
    assert body["auto_submit"] is False
    assert body["submission_mode"] == "manual_review_only"
    assert body["opportunity_id"] == opp_id
    assert "Gui" in body["outreach_draft_en"]
    assert "config/profile.yml" in body["provenance"]["candidate_sources"]

    assert client.post("/api/opportunities/nonexistent_id/prepare").status_code == 404
