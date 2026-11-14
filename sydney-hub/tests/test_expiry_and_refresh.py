from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from hub.discovery.expiry import (
    CONSECUTIVE_MISS_EXPIRY_THRESHOLD,
    is_explicit_expiry,
    is_transient_or_unknown,
    select_incremental_candidates,
    should_recheck,
)
from hub.discovery.published import ListingCheck, PublishedListing, SourcePolicy
from hub.storage.catalog import CatalogRepository
from hub.storage.db import (
    Database,
    ensure_lifecycle_schema,
    migrate_lifecycle_catalog,
    migrate_published_catalog,
    restore_catalog_backup,
)
from hub.storage.jobs_bootstrap import ISOLATED_DB_MARKER
from run_published_jobs import run_published_jobs


def _make_v5_db(path):
    Database(path)
    migrate_published_catalog(path)


def test_v5_to_v6_migration_and_rollback_preserves_data(tmp_path):
    db_path = tmp_path / "hub.db"
    _make_v5_db(db_path)
    catalog = CatalogRepository(Database(db_path))
    now = datetime.now(timezone.utc)
    listing = PublishedListing(
        "workable", "W1", "https://apply.workable.com/cafe/j/W1/",
        "Barista", "Cafe", "Rockdale NSW", now - timedelta(days=1),
        "Casual", None, None, None,
    )
    opp_id = catalog.upsert_published_listing(
        listing,
        ListingCheck("ok", now.timestamp(), listing.source_url, "Barista"),
        SourcePolicy("workable", True, "Jobs by Workable"),
        role_bucket="barista",
    )

    backup = migrate_lifecycle_catalog(db_path)
    assert backup.is_file()
    with Database(db_path).connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # Mutate in v6, then force-rollback to v5
    with Database(db_path).connect() as conn:
        conn.execute("UPDATE opportunities SET lifecycle_state='expired' WHERE id=?", (opp_id,))
    safety = restore_catalog_backup(db_path, force=True, target_version=6)
    assert safety is not None and safety.is_file()
    with Database(db_path).connect() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM opportunities WHERE id=?", (opp_id,)).fetchone()[0] == 1


def test_transient_errors_never_expire_while_explicit_closed_or_two_misses_expire(tmp_path):
    db_path = tmp_path / "hub.db"
    _make_v5_db(db_path)
    migrate_lifecycle_catalog(db_path)
    catalog = CatalogRepository(Database(db_path))
    now = datetime.now(timezone.utc)

    l1 = PublishedListing(
        "ats:greenhouse:cafe", "G1", "https://boards.greenhouse.io/cafe/jobs/1",
        "Barista", "Cafe", "Rockdale NSW", now - timedelta(days=2),
        "Casual", None, None, None,
    )
    l2 = PublishedListing(
        "ats:greenhouse:cafe", "G2", "https://boards.greenhouse.io/cafe/jobs/2",
        "Kitchen Hand", "Cafe", "Rockdale NSW", now - timedelta(days=2),
        "Casual", None, None, None,
    )
    id1 = catalog.upsert_published_listing(
        l1, ListingCheck("ok", now.timestamp(), l1.source_url, "Barista"),
        SourcePolicy("ats:greenhouse:cafe", True, "Cafe"),
    )
    id2 = catalog.upsert_published_listing(
        l2, ListingCheck("ok", now.timestamp(), l2.source_url, "Kitchen Hand"),
        SourcePolicy("ats:greenhouse:cafe", True, "Cafe"),
    )

    assert is_transient_or_unknown("timeout_or_network")
    assert is_transient_or_unknown("http_429")
    assert is_transient_or_unknown("http_403")
    assert is_explicit_expiry("closed_or_expired")
    assert is_explicit_expiry("http_404")
    assert is_explicit_expiry("http_410")

    # Network errors / 429 / 403 MUST NOT expire id1
    for outcome in ("timeout_or_network", "http_429", "http_403", "http_503"):
        state = catalog.apply_listing_check_lifecycle(
            id1, ListingCheck(outcome, now.timestamp() + 10, l1.source_url, None)
        )
        assert state == "active"

    # Failed board fetch MUST NOT count as a miss
    res_fail = catalog.apply_board_reconciliation("ats:greenhouse:cafe", set(), fetch_succeeded=False)
    assert res_fail == {"missed": 0, "expired": 0}

    # 1st successful board fetch missing G1 -> 1 miss, still active
    res_miss1 = catalog.apply_board_reconciliation("ats:greenhouse:cafe", {"G2"}, fetch_succeeded=True)
    assert res_miss1["missed"] == 1 and res_miss1["expired"] == 0

    # 2nd consecutive successful board fetch missing G1 -> expired!
    res_miss2 = catalog.apply_board_reconciliation("ats:greenhouse:cafe", {"G2"}, fetch_succeeded=True)
    assert CONSECUTIVE_MISS_EXPIRY_THRESHOLD == 2
    assert res_miss2["expired"] == 1

    # Explicit 404/closed on G2 expires immediately while preserving record
    state2 = catalog.apply_listing_check_lifecycle(
        id2, ListingCheck("closed_or_expired", now.timestamp() + 20, l2.source_url, None)
    )
    assert state2 == "expired"
    by_id = {row["id"]: row for row in catalog.list()}
    assert by_id[id1]["lifecycle_state"] == "expired"
    assert by_id[id2]["lifecycle_state"] == "expired"


def test_stale_marking_and_incremental_refresh_skip_recently_checked(tmp_path):
    db_path = tmp_path / "hub.db"
    _make_v5_db(db_path)
    migrate_lifecycle_catalog(db_path)
    catalog = CatalogRepository(Database(db_path))
    now = datetime.now(timezone.utc)
    old_ts = (now - timedelta(days=10)).timestamp()

    old_listing = PublishedListing(
        "workable", "OLD1", "https://apply.workable.com/cafe/j/OLD1/",
        "Barista", "Cafe", "Rockdale NSW", now - timedelta(days=12),
        "Casual", None, None, None,
    )
    old_id = catalog.upsert_published_listing(
        old_listing,
        ListingCheck("ok", old_ts, old_listing.source_url, "Barista"),
        SourcePolicy("workable", True, "Jobs by Workable"),
    )
    marked = catalog.mark_stale_opportunities(stale_after_days=7, now=now)
    assert marked == 1
    item = catalog.list()[0]
    assert item["id"] == old_id
    assert item["lifecycle_state"] == "stale"
    assert item["is_stale"] is True

    fresh_iso = (now - timedelta(hours=2)).isoformat()
    stale_iso = (now - timedelta(days=10)).isoformat()
    assert should_recheck(None, now=now) is True
    assert should_recheck(fresh_iso, now=now, recheck_interval_hours=24) is False
    assert should_recheck(fresh_iso, now=now, recheck_interval_hours=24, force_recheck=True) is True
    assert should_recheck(stale_iso, now=now, recheck_interval_hours=24) is True

    fresh_listing = PublishedListing(
        "workable", "NEW1", "https://apply.workable.com/cafe/j/NEW1/",
        "Cleaner", "Cafe", "Mascot NSW", now - timedelta(hours=5),
        "Casual", None, None, None,
    )
    to_check, skipped = select_incremental_candidates(
        [(fresh_listing, None), (old_listing, None)],
        {
            "https://apply.workable.com/cafe/j/NEW1/": fresh_iso,
            "https://apply.workable.com/cafe/j/OLD1/": stale_iso,
        },
        now=now,
        recheck_interval_hours=24,
    )
    assert skipped == 1
    assert [item.external_id for item, _ in to_check] == ["OLD1"]


def test_incremental_refresh_in_runner_skips_fresh_url(tmp_path, monkeypatch):
    isolated_root = tmp_path / "jobs_isolated"
    isolated_root.mkdir(parents=True)
    (isolated_root / ISOLATED_DB_MARKER).write_text("isolated\n", encoding="utf-8")
    db_path = isolated_root / "jobs_hub.db"
    ensure_lifecycle_schema(db_path, isolated_root=isolated_root)

    boards_path = tmp_path / "boards.yml"
    boards_path.write_text("boards: []\n", encoding="utf-8")
    policies_path = tmp_path / "policies.yml"
    policies_path.write_text(
        "sources:\n"
        "  workable:\n"
        "    publish_allowed: true\n"
        "    attribution: Jobs by Workable\n"
        "    terms_url: https://help.workable.com/hc/en-us/articles/4420464031767-Utilizing-the-XML-Job-Feed\n"
        "    terms_reviewed_at: '2026-11-14'\n"
        "    access_method: public_xml_feed\n"
        "    retention_days: 30\n"
        "    max_requests: 10\n"
        "    cost_cap_aud: 0.0\n",
        encoding="utf-8",
    )

    now = datetime.now(timezone.utc)
    listing = PublishedListing(
        "workable", "INC1", "https://apply.workable.com/cafe/j/INC1/",
        "Barista", "Cafe", "Rockdale NSW", now - timedelta(hours=3),
        "Casual", None, None, None,
    )
    verify_calls = []

    monkeypatch.setattr("run_published_jobs.fetch_workable", lambda _http: [listing])

    def fake_verify(item, _client):
        verify_calls.append(item.source_url)
        return ListingCheck("ok", time_now(), item.source_url, item.title), None, 100

    def time_now():
        return datetime.now(timezone.utc).timestamp()

    monkeypatch.setattr("run_published_jobs._verify_job_page_with_location", fake_verify)

    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200)))
    r1 = run_published_jobs(
        db_path, boards_path, live=True, client=client,
        policies_path=policies_path, state_dir=tmp_path / "state",
        reports_dir=tmp_path / "reports", isolated_root=isolated_root,
        recheck_interval_hours=24.0,
    )
    assert r1["sources"]["workable"]["verified"] == 1
    assert len(verify_calls) == 1

    # Second run within 24h skips re-verifying the fresh listing!
    r2 = run_published_jobs(
        db_path, boards_path, live=True, client=client,
        policies_path=policies_path, state_dir=tmp_path / "state",
        reports_dir=tmp_path / "reports", isolated_root=isolated_root,
        recheck_interval_hours=24.0,
    )
    assert r2["sources"]["workable"]["skipped_fresh"] == 1
    assert len(verify_calls) == 1
