"""One bounded, manual published-jobs run. Zero hardcoded approvals or scheduler self-activation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import httpx

from hub.discovery.adzuna import evaluate_adzuna_au
from hub.discovery.ats_bridge import AtsBridge
from hub.discovery.ats_filter import _strip_html
from hub.discovery.geocode import (
    ExtractedLocationEvidence,
    extract_posting_location_evidence,
    geocode_explicit_street_address,
)
from hub.discovery.health import (
    SourceHealthSnapshot,
    SourceHealthStore,
    append_yield_journal,
    parse_retry_after,
    redact_secrets,
)
from hub.discovery.pilot_gate import (
    confirm_pilot_audit,
    evaluate_pilot_gates,
    export_pilot_audit_template,
)
from hub.discovery.published import ListingCheck, PublishedListing, SourcePolicy
from hub.discovery.published_ats import (
    audit_official_boards_manifest,
    fetch_board_listings,
    load_official_boards,
    validate_board_provenance,
    validate_official_board,
)
from hub.discovery.published_filter import classify_published
from hub.discovery.source_policy import (
    load_source_policies,
    resolve_board_policy,
    resolve_source_policy,
)
from hub.discovery.transit import measure_commute, reference_departures, resolve_origin_stop
from hub.discovery.website import _public_url
from hub.discovery.workable import WORKABLE_FEED_URL, fetch_workable
from hub.settings import load_jobs_settings
from hub.storage.catalog import CatalogRepository
from hub.discovery.expiry import select_incremental_candidates
from hub.storage.db import (
    Database,
    ensure_lifecycle_schema,
    ensure_published_schema,
    snapshot_published_run,
    validate_isolated_jobs_db_path,
)


_CLOSED_SIGNALS_RE = re.compile(
    r"\b(?:applications?\s+(?:are\s+)?closed|position\s+(?:has\s+been\s+)?filled|"
    r"no\s+longer\s+accepting\s+applications?|vacancy\s+closed|"
    r"this\s+job\s+is\s+no\s+longer\s+available|job\s+has\s+expired|listing\s+expired)\b",
    re.I,
)
_HEADING_OR_TITLE_RE = re.compile(r"<(?:title|h1|h2)\b[^>]*>(.*?)</(?:title|h1|h2)>", re.I | re.S)
_FOOTER_BLOCK_RE = re.compile(r"<footer\b[^>]*>.*?</footer>", re.I | re.S)
_APPLY_CONTROL_RE = re.compile(
    r"<(?:button|a|form|input)\b[^>]*>[^<]*\bapply(?:\s+(?:now|here|today|for\s+this))?\b[^<]*</(?:button|a|form)|"
    r"<input\b[^>]*value=[\"'][^\"']*\bapply\b[^\"']*[\"'][^>]*>",
    re.I | re.S,
)
_SYDNEY_TZ = ZoneInfo("Australia/Sydney")


def _workable_api_url(canonical_url: str) -> tuple[str, str] | None:
    parsed = urlsplit(canonical_url)
    if parsed.scheme != "https" or parsed.hostname != "apply.workable.com" or parsed.port not in {None, 443}:
        return None
    matched = re.fullmatch(r"/([a-zA-Z0-9_-]+)/j/([a-zA-Z0-9]+)/?", parsed.path)
    if not matched:
        return None
    account, shortcode = matched.group(1), matched.group(2)
    return f"https://apply.workable.com/api/v2/accounts/{account}/jobs/{shortcode}", shortcode


def _verify_workable_api(
    listing: PublishedListing,
    canonical_url: str,
    client: httpx.Client,
    checked_at: float,
) -> tuple[ListingCheck, ExtractedLocationEvidence | None]:
    info = _workable_api_url(canonical_url)
    if info is None or not _public_url(canonical_url):
        return ListingCheck("unconfirmed", checked_at, listing.source_url, None), None
    api_url, shortcode = info
    if not _public_url(api_url):
        return ListingCheck("blocked", checked_at, listing.source_url, None), None
    try:
        with client.stream("GET", api_url, follow_redirects=False, timeout=8.0) as response:
            if response.status_code != 200 or "json" not in response.headers.get("content-type", "").lower():
                outcome = "unconfirmed" if response.status_code == 200 else f"http_{response.status_code}"
                return ListingCheck(outcome, checked_at, listing.source_url, None), None
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > 524288:
                    return ListingCheck("oversize", checked_at, listing.source_url, None), None
                chunks.append(chunk)
    except httpx.HTTPError:
        return ListingCheck("timeout_or_network", checked_at, listing.source_url, None), None
    raw_json = b"".join(chunks).decode("utf-8", errors="replace")
    try:
        data = json.loads(raw_json)
    except ValueError:
        return ListingCheck("unconfirmed", checked_at, listing.source_url, None), None
    if isinstance(data, dict) and str(data.get("state") or "").lower() in {"closed", "archived", "expired"}:
        return ListingCheck("closed_or_expired", checked_at, listing.source_url, None), None
    expected_title = " ".join(listing.title.casefold().split())
    api_title = " ".join(str(data.get("title") or "").casefold().split()) if isinstance(data, dict) else ""
    if (
        isinstance(data, dict)
        and data.get("state") == "published"
        and str(data.get("shortcode") or "") == shortcode
        and expected_title
        and api_title == expected_title
    ):
        loc_ev = extract_posting_location_evidence(
            raw_json, content_type="application/json", evidence_url=canonical_url
        )
        return ListingCheck("ok", checked_at, canonical_url, listing.title), loc_ev
    return ListingCheck("unconfirmed", checked_at, listing.source_url, None), None


def _verify_job_page_with_location(
    listing: PublishedListing,
    client: httpx.Client,
) -> tuple[ListingCheck, ExtractedLocationEvidence | None, int]:
    checked_at = time.time()
    if not _public_url(listing.source_url):
        return ListingCheck("blocked", checked_at, listing.source_url, None), None, 0
    wire_bytes = 0
    try:
        with client.stream("GET", listing.source_url, follow_redirects=False, timeout=8.0) as response:
            if response.status_code in (301, 302, 307, 308):
                location = response.headers.get("location")
                if (
                    listing.source_id == "workable"
                    and urlsplit(listing.source_url).hostname == "apply.workable.com"
                    and location
                ):
                    target = urljoin(listing.source_url, location)
                    if _workable_api_url(target) is not None and _public_url(target):
                        check, loc_ev = _verify_workable_api(listing, target, client, checked_at)
                        return check, loc_ev, 0
                return ListingCheck(f"http_{response.status_code}", checked_at, listing.source_url, None), None, 0
            if response.status_code != 200 or "html" not in response.headers.get("content-type", "").lower():
                return ListingCheck(f"http_{response.status_code}", checked_at, listing.source_url, None), None, 0
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > 524288:
                    return ListingCheck("oversize", checked_at, listing.source_url, None), None, total
                chunks.append(chunk)
            wire_bytes = total
    except httpx.HTTPError:
        return ListingCheck("timeout_or_network", checked_at, listing.source_url, None), None, wire_bytes

    html = b"".join(chunks).decode("utf-8", errors="replace")
    plain_text = _strip_html(html)
    if _CLOSED_SIGNALS_RE.search(plain_text):
        return ListingCheck("closed_or_expired", checked_at, listing.source_url, None), None, wire_bytes

    expected_title = " ".join(listing.title.casefold().split())
    headings = " ".join(_strip_html(h).casefold() for h in _HEADING_OR_TITLE_RE.findall(html))
    main_html = _FOOTER_BLOCK_RE.sub(" ", html)
    has_heading_match = bool(expected_title and expected_title in headings)
    has_apply_control = bool(_APPLY_CONTROL_RE.search(main_html))
    loc_ev = extract_posting_location_evidence(
        html, content_type="text/html", evidence_url=listing.source_url
    )

    if has_heading_match and has_apply_control:
        return ListingCheck("ok", checked_at, listing.source_url, listing.title), loc_ev, wire_bytes

    if listing.source_id == "workable" and _workable_api_url(listing.source_url) is not None:
        check, api_ev = _verify_workable_api(listing, listing.source_url, client, checked_at)
        return check, api_ev or loc_ev, wire_bytes

    return ListingCheck("unconfirmed", checked_at, listing.source_url, None), loc_ev, wire_bytes


def verify_job_page(listing: PublishedListing, client: httpx.Client) -> ListingCheck:
    """Verify a listing's specific URL without falling back to generic footer apply links."""
    check, _, _ = _verify_job_page_with_location(listing, client)
    return check


_DEFAULT_VERIFY_JOB_PAGE = verify_job_page


def _listing_identity(item: PublishedListing) -> str:
    if item.external_id:
        return f"ext:{item.external_id}"
    return f"url:{item.source_url.strip().lower()}"


def _dedup_listings(listings: list[PublishedListing]) -> tuple[list[PublishedListing], int]:
    seen: set[str] = set()
    unique: list[PublishedListing] = []
    dupes = 0
    for item in listings:
        key = _listing_identity(item)
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        unique.append(item)
    return unique, dupes


def _default_report_dict() -> dict:
    return {
        "fetched": 0,
        "eligible": 0,
        "verified": 0,
        "unverified": 0,
        "checked": 0,
        "skipped_fresh": 0,
        "new_count": 0,
        "updated_count": 0,
        "duplicate_within_source": 0,
        "recent_count": 0,
        "explicit_modality_count": 0,
        "exact_coords_count": 0,
        "requests": 0,
        "wire_bytes": 0,
        "cost_aud": 0.0,
        "last_outcome": "ok",
        "error": None,
    }


def _record_source_health(
    store: SourceHealthStore,
    source_id: str,
    summary: dict,
    *,
    duration_ms: int,
    cooldown_until: float | None = None,
    retry_after_seconds: int | None = None,
    license_label: str = "",
) -> None:
    prev = store.get(source_id) or {}
    failures = int(prev.get("consecutive_failures") or 0)
    outcome = summary["last_outcome"]
    if outcome in {"error", "cooldown"}:
        failures += 1
    elif outcome == "ok":
        failures = 0
    reqs = int(summary["requests"])
    snap = SourceHealthSnapshot(
        source_id=source_id,
        last_run_at=datetime.now(timezone.utc).isoformat(),
        last_outcome=outcome,
        cooldown_until=cooldown_until,
        retry_after_seconds=retry_after_seconds,
        consecutive_failures=failures,
        last_error=summary["error"],
        requests=reqs,
        wire_bytes=int(summary["wire_bytes"]),
        duration_ms=duration_ms,
        fetched=int(summary["fetched"]),
        eligible=int(summary["eligible"]),
        new_count=int(summary["new_count"]),
        updated_count=int(summary["updated_count"]),
        duplicate_within_source=int(summary["duplicate_within_source"]),
        verified_count=int(summary["verified"]),
        unverified_count=int(summary["unverified"]),
        recent_count=int(summary["recent_count"]),
        explicit_modality_count=int(summary["explicit_modality_count"]),
        exact_coords_count=int(summary["exact_coords_count"]),
        yield_per_request=round(summary["verified"] / reqs, 4) if reqs else 0.0,
        cost_aud=float(summary["cost_aud"]),
        license=license_label,
    )
    store.record_result(snap)


def run_published_jobs(
    db_path: Path,
    boards_path: Path,
    *,
    live: bool = False,
    client: httpx.Client | None = None,
    bridge: AtsBridge | None = None,
    max_verifications: int = 30,
    evaluate_adzuna: bool = False,
    policies_path: Path | None = None,
    state_dir: Path | None = None,
    reports_dir: Path | None = None,
    isolated_root: Path | None = None,
    skip_workable: bool = False,
    geocode_street_addresses: bool = False,
    origin_label: str = "Rockdale Station",
    recheck_interval_hours: float | None = None,
    force_recheck: bool = False,
) -> dict:
    """Execute one bounded Jobs run with persisted fail-closed policies and isolated DB guard."""
    root_dir = Path(__file__).resolve().parent
    resolved_policies = policies_path or (root_dir / "config" / "source_policies.yml")
    resolved_state = state_dir or (root_dir / "data" / "published-state")
    resolved_reports = reports_dir or (root_dir / "data" / "pilot-reports")
    health_store = SourceHealthStore(resolved_state)
    policies = load_source_policies(resolved_policies)

    catalog = None
    existing_keys: set[tuple[str, str]] = set()
    existing_url_keys: set[str] = set()
    existing_verified_map: dict[str, str | None] = {}
    if live:
        validated_db = validate_isolated_jobs_db_path(db_path, isolated_root=isolated_root)
        ensure_published_schema(validated_db, isolated_root=isolated_root)
        ensure_lifecycle_schema(validated_db, isolated_root=isolated_root)
        snapshot_published_run(validated_db, isolated_root=isolated_root)
        db = Database(validated_db)
        catalog = CatalogRepository(db)
        existing_verified_map = catalog.get_last_verified_map()
        with db.connect() as conn:
            existing_keys = {
                (r[0], r[1]) for r in conn.execute("SELECT source_id, external_id FROM published_source_keys").fetchall()
            }
            existing_url_keys = {
                r[0] for r in conn.execute("SELECT source_url FROM opportunities").fetchall()
            }

    now_utc = datetime.now(timezone.utc)
    now_ts = now_utc.timestamp()
    report: dict = {
        "mode": "live" if live else "dry-run",
        "run_at": now_utc.isoformat(),
        "sydney_date": now_utc.astimezone(_SYDNEY_TZ).date().isoformat(),
        "sources": {},
        "transit": {"measured": 0, "unknown": 0},
        "collisions": 0,
        "cost_aud": 0.0,
    }
    max_verifications = max(0, min(int(max_verifications), 50))
    context = nullcontext(client) if client is not None else httpx.Client()

    with context as http:
        tfnsw_key = os.getenv("TFNSW_API_KEY") if os.getenv("TFNSW_FREE_QUOTA_CONFIRMED") == "1" else None
        origin_resolved = False
        cached_origin: str | None = None

        def get_origin_stop() -> str | None:
            nonlocal origin_resolved, cached_origin
            if not origin_resolved:
                origin_resolved = True
                cached_origin = resolve_origin_stop(http, api_key=tfnsw_key) if tfnsw_key else None
            return cached_origin

        def process(source: str, listings: list[PublishedListing], feed_evidence_url: str, policy: SourcePolicy) -> dict:
            started = time.monotonic()
            summary = report["sources"].setdefault(source, _default_report_dict())
            summary["requests"] += 1

            if live and not policy.publish_allowed:
                summary["last_outcome"] = "skipped_unapproved"
                summary["error"] = "source_policy_unapproved"
                _record_source_health(
                    health_store,
                    source,
                    summary,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    license_label=policy.attribution or source,
                )
                return summary

            unique, dupes = _dedup_listings(listings)
            summary["fetched"] += len(listings)
            summary["duplicate_within_source"] += dupes

            eligible = [(item, classify_published(item)) for item in unique]
            eligible = [(item, match) for item, match in eligible if match.role_bucket and match.region_eligible is not False]
            summary["eligible"] += len(eligible)
            eligible.sort(key=lambda pair: pair[0].published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

            if catalog is not None and recheck_interval_hours is not None and recheck_interval_hours > 0 and not force_recheck:
                candidates, skipped_fresh = select_incremental_candidates(
                    eligible,
                    existing_verified_map,
                    now=now_utc,
                    recheck_interval_hours=recheck_interval_hours,
                    force_recheck=force_recheck,
                )
                summary["skipped_fresh"] += skipped_fresh
            else:
                candidates = eligible

            cap = max_verifications if policy.max_requests <= 0 else min(max_verifications, policy.max_requests)
            for item, match in candidates[: max(0, cap - summary["checked"])]:
                if verify_job_page is _DEFAULT_VERIFY_JOB_PAGE:
                    check, loc_ev, page_bytes = _verify_job_page_with_location(item, http)
                else:
                    check = verify_job_page(item, http)
                    loc_ev, page_bytes = None, 0
                summary["requests"] += 1
                summary["wire_bytes"] += page_bytes
                summary["checked"] += 1
                if check.outcome == "ok":
                    summary["verified"] += 1
                else:
                    summary["unverified"] += 1

                lat, lng = item.lat, item.lng
                if (lat is None or lng is None) and loc_ev is not None:
                    if loc_ev.lat is not None and loc_ev.lng is not None:
                        lat, lng = loc_ev.lat, loc_ev.lng
                    elif geocode_street_addresses and loc_ev.street_address:
                        geocoded = geocode_explicit_street_address(
                            loc_ev.street_address,
                            client=http,
                            tfnsw_api_key=tfnsw_key,
                            enabled=True,
                        )
                        if geocoded is not None:
                            lat, lng = geocoded

                persisted_item = replace(item, source_id=source, lat=lat, lng=lng)
                effective_match = classify_published(persisted_item)
                if check.outcome == "ok":
                    if persisted_item.published_at is not None and (now_utc - persisted_item.published_at).days <= 30:
                        summary["recent_count"] += 1
                    if persisted_item.employment_type:
                        summary["explicit_modality_count"] += 1
                    if effective_match.location_tier == "exact":
                        summary["exact_coords_count"] += 1

                if catalog is None:
                    continue

                ext_key = persisted_item.external_id or hashlib.sha256(persisted_item.source_url.encode()).hexdigest()[:20]
                is_existing = (source, ext_key) in existing_keys
                if not is_existing and persisted_item.source_url in existing_url_keys:
                    report["collisions"] += 1
                if is_existing:
                    summary["updated_count"] += 1
                else:
                    summary["new_count"] += 1
                    existing_keys.add((source, ext_key))
                    existing_url_keys.add(persisted_item.source_url)

                # Keep Workable <url> verbatim in persisted_item.source_url; record verified URL as evidence_url.
                verified_evidence_url = check.evidence_url or feed_evidence_url
                persisted_check = ListingCheck(
                    check.outcome,
                    check.checked_at,
                    verified_evidence_url,
                    check.excerpt,
                )
                opportunity_id = catalog.upsert_published_listing(
                    persisted_item, persisted_check, policy, role_bucket=effective_match.role_bucket
                )
                catalog.apply_listing_check_lifecycle(opportunity_id, persisted_check, now_iso=now_utc.isoformat())
                if persisted_check.outcome == "ok":
                    existing_verified_map[persisted_item.source_url.strip().lower()] = now_utc.isoformat()
                if check.outcome != "ok" or effective_match.location_tier != "exact":
                    report["transit"]["unknown"] += 1
                    continue
                if not tfnsw_key:
                    report["transit"]["unknown"] += 1
                    continue
                origin = get_origin_stop()
                if not origin:
                    report["transit"]["unknown"] += 1
                    continue
                for window, departure in reference_departures(datetime.now(_SYDNEY_TZ).date()):
                    result = measure_commute(origin, (persisted_item.lat, persisted_item.lng), departure, client=http, api_key=tfnsw_key)
                    if catalog.record_commute_observation(opportunity_id, origin_label, window, departure, result):
                        report["transit"]["measured"] += 1
                    else:
                        report["transit"]["unknown"] += 1

            if catalog is not None and unique:
                seen_ext_ids = {
                    (it.external_id or hashlib.sha256(it.source_url.encode()).hexdigest()[:20])
                    for it in unique
                }
                catalog.apply_board_reconciliation(source, seen_ext_ids, fetch_succeeded=True, now_iso=now_utc.isoformat())
                catalog.mark_stale_opportunities(now=now_utc)

            summary["last_outcome"] = "ok"
            _record_source_health(
                health_store,
                source,
                summary,
                duration_ms=int((time.monotonic() - started) * 1000),
                license_label=policy.attribution or source,
            )
            return summary

        # --- Workable XML source (strictly fail-closed on policy + 429 cooldown without bypass) ---
        workable_policy = resolve_source_policy(policies, "workable")
        in_cd, cd_until = health_store.in_cooldown("workable", now_ts)
        if skip_workable:
            w_sum = report["sources"].setdefault("workable", _default_report_dict())
            w_sum["last_outcome"] = "skipped"
            _record_source_health(health_store, "workable", w_sum, duration_ms=0, license_label=workable_policy.attribution)
        elif in_cd:
            w_sum = report["sources"].setdefault("workable", _default_report_dict())
            w_sum["last_outcome"] = "cooldown"
            w_sum["error"] = "active_429_cooldown"
            _record_source_health(
                health_store, "workable", w_sum, duration_ms=0, cooldown_until=cd_until, license_label=workable_policy.attribution
            )
        elif live and not workable_policy.publish_allowed:
            process("workable", [], WORKABLE_FEED_URL, workable_policy)
        else:
            w_start = time.monotonic()
            for attempt in range(2):
                try:
                    listings = fetch_workable(http)
                    health_store.record_workable_fetch(time.time())
                    process("workable", listings, WORKABLE_FEED_URL, workable_policy)
                    break
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    raw_ra = (exc.response.headers.get("retry-after") or "").strip()
                    clean_ra = re.sub(r"[^A-Za-z0-9_.:-]", "", raw_ra)[:32]
                    error_label = f"http_{status_code}_retry_after_{clean_ra}" if clean_ra else f"http_{status_code}"
                    if status_code == 429 or 400 <= status_code < 500 or attempt == 1:
                        w_sum = report["sources"].setdefault("workable", _default_report_dict())
                        w_sum["requests"] += 1
                        w_sum["error"] = error_label
                        ra_secs = parse_retry_after(raw_ra, now=now_ts) if status_code == 429 else None
                        until = (now_ts + ra_secs) if ra_secs is not None else None
                        w_sum["last_outcome"] = "cooldown" if status_code == 429 else "error"
                        _record_source_health(
                            health_store,
                            "workable",
                            w_sum,
                            duration_ms=int((time.monotonic() - w_start) * 1000),
                            cooldown_until=until,
                            retry_after_seconds=ra_secs,
                            license_label=workable_policy.attribution,
                        )
                        break
                    time.sleep(1.0)
                except httpx.HTTPError as exc:
                    if attempt == 0:
                        time.sleep(1.0)
                        continue
                    w_sum = report["sources"].setdefault("workable", _default_report_dict())
                    w_sum["requests"] += 1
                    w_sum["error"] = type(exc).__name__
                    w_sum["last_outcome"] = "error"
                    _record_source_health(
                        health_store, "workable", w_sum, duration_ms=int((time.monotonic() - w_start) * 1000), license_label=workable_policy.attribution
                    )
                except (ValueError, RuntimeError) as exc:
                    w_sum = report["sources"].setdefault("workable", _default_report_dict())
                    w_sum["requests"] += 1
                    w_sum["error"] = str(exc)[:120]
                    w_sum["last_outcome"] = "error"
                    _record_source_health(
                        health_store, "workable", w_sum, duration_ms=int((time.monotonic() - w_start) * 1000), license_label=workable_policy.attribution
                    )
                    break

        # --- Official ATS boards (strictly per-board policy ats:<provider>:<slug>) ---
        try:
            boards = load_official_boards(boards_path)
        except FileNotFoundError:
            boards = []
            report["sources"]["ats_manifest"] = {"error": "manifest_missing"}
        except ValueError as exc:
            boards = []
            report["sources"]["ats_manifest"] = {"error": str(exc)[:120]}
        if boards and bridge is None:
            bridge = AtsBridge(Path(__file__).resolve().parents[1] / "providers")
        for board in boards[:10]:
            board_source = board.policy_source_id
            board_policy = resolve_board_policy(policies, board.provider_id, board.slug)
            if live and not board_policy.publish_allowed:
                process(board_source, [], board.board_url, board_policy)
                continue
            b_start = time.monotonic()
            try:
                prov_ok, prov_reason = validate_board_provenance(board)
                if not prov_ok:
                    raise ValueError(prov_reason)
                if not validate_official_board(board, http):
                    raise ValueError("Official career-page link not verified")
                board_listings = fetch_board_listings(board, bridge, include_board_slug=True)
                process(board_source, board_listings, board.board_url, board_policy)
            except (ValueError, RuntimeError, httpx.HTTPError) as exc:
                b_sum = report["sources"].setdefault(board_source, _default_report_dict())
                b_sum["requests"] += 1
                b_sum["last_outcome"] = "error"
                b_sum["error"] = str(exc)[:120]
                _record_source_health(
                    health_store,
                    board_source,
                    b_sum,
                    duration_ms=int((time.monotonic() - b_start) * 1000),
                    license_label=board_policy.attribution or board_source,
                )

        if evaluate_adzuna:
            if os.getenv("ADZUNA_FREE_QUOTA_CONFIRMED") != "1":
                report["adzuna"] = {"status": "skipped_free_quota_unconfirmed"}
            elif not os.getenv("ADZUNA_APP_ID") or not os.getenv("ADZUNA_APP_KEY"):
                report["adzuna"] = {"status": "skipped_credentials_missing"}
            else:
                result = evaluate_adzuna_au(
                    http,
                    app_id=os.environ["ADZUNA_APP_ID"],
                    app_key=os.environ["ADZUNA_APP_KEY"],
                    queries=["barista", "kitchen hand", "housekeeping"],
                    max_pages=1,
                )
                report["adzuna"] = {
                    "status": "evaluated",
                    "requests": result.requests,
                    "ads_seen": result.ads_seen,
                    "errors": result.errors,
                }

    totals = report["sources"].values()
    journal_entry = {
        "run_at": report["run_at"],
        "sydney_date": report["sydney_date"],
        "mode": report["mode"],
        "requests": sum(int(s.get("requests", 0)) for s in totals if isinstance(s, dict)),
        "wire_bytes": sum(int(s.get("wire_bytes", 0)) for s in totals if isinstance(s, dict)),
        "fetched": sum(int(s.get("fetched", 0)) for s in totals if isinstance(s, dict)),
        "eligible": sum(int(s.get("eligible", 0)) for s in totals if isinstance(s, dict)),
        "verified": sum(int(s.get("verified", 0)) for s in totals if isinstance(s, dict)),
        "unverified": sum(int(s.get("unverified", 0)) for s in totals if isinstance(s, dict)),
        "new_count": sum(int(s.get("new_count", 0)) for s in totals if isinstance(s, dict)),
        "updated_count": sum(int(s.get("updated_count", 0)) for s in totals if isinstance(s, dict)),
        "duplicate_within_source": sum(int(s.get("duplicate_within_source", 0)) for s in totals if isinstance(s, dict)),
        "collisions": report["collisions"],
        "transit_measured": report["transit"]["measured"],
        "transit_unknown": report["transit"]["unknown"],
        "cost_aud": report["cost_aud"],
    }
    append_yield_journal(resolved_reports, journal_entry)
    return redact_secrets(report)


def main(argv: list[str] | None = None) -> int:
    root_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description="Run one bounded published jobs discovery pass")
    parser.add_argument("--config", type=Path, default=root_dir / "config" / "jobs.yml")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--isolated-root", type=Path, default=None)
    parser.add_argument("--policies", type=Path, default=None)
    parser.add_argument("--boards", type=Path, default=None)
    parser.add_argument("--state-dir", type=Path, default=None)
    parser.add_argument("--reports-dir", type=Path, default=None)
    parser.add_argument("--report-out", type=Path, default=None)
    parser.add_argument("--live", action="store_true", help="Write verified/unverified rows inside isolated DB after snapshot")
    parser.add_argument("--evaluate-adzuna", action="store_true", help="Private coverage evaluation only")
    parser.add_argument("--skip-workable", action="store_true", help="Skip Workable XML feed fetch during this run")
    parser.add_argument("--audit-boards", action="store_true", help="Audit official ATS boards manifest without running jobs")
    parser.add_argument("--pilot-status", action="store_true", help="Print honest 7-day pilot scorecard and exit")
    parser.add_argument("--export-audit", type=Path, default=None, help="Export 100%% verified_job audit checklist JSON")
    parser.add_argument("--confirm-pilot-audit", action="store_true", help="Confirm pilot audit using --audit-file JSON")
    parser.add_argument("--audit-file", type=Path, default=None, help="Path to completed per-listing audit JSON")
    parser.add_argument("--geocode-street-addresses", action="store_true", help="Allow free geocoding of explicit NSW street addresses")
    parser.add_argument("--recheck-hours", type=float, default=24.0, help="Skip re-verifying listings checked within N hours (0 disables)")
    parser.add_argument("--force-recheck", action="store_true", help="Bypass incremental verification skip window")
    parser.add_argument("--max-verifications", type=int, default=30)
    args = parser.parse_args(argv)

    cfg = load_jobs_settings(args.config)
    db_path = args.db or cfg.resolve(cfg.published_jobs.db_path)
    isolated_root = args.isolated_root or cfg.resolve(cfg.published_jobs.isolated_db_dir)
    policies_path = args.policies or cfg.resolve(cfg.published_jobs.policies_path)
    boards_path = args.boards or cfg.resolve(cfg.published_jobs.boards_path)
    state_dir = args.state_dir or cfg.resolve(cfg.published_jobs.state_dir)
    reports_dir = args.reports_dir or cfg.resolve(cfg.published_jobs.reports_dir)

    if args.audit_boards:
        with httpx.Client() as http:
            audit_rows = [
                {
                    "company": r.company,
                    "provider_id": r.provider_id,
                    "slug": r.slug,
                    "board_url": r.board_url,
                    "valid": r.valid,
                    "reason": r.reason,
                }
                for r in audit_official_boards_manifest(boards_path, http)
            ]
        print(json.dumps({"boards": audit_rows}, indent=2, ensure_ascii=False))
        return 0

    if args.export_audit is not None:
        validate_isolated_jobs_db_path(db_path, isolated_root=isolated_root)
        rows = export_pilot_audit_template(db_path, args.export_audit)
        print(json.dumps({"exported": len(rows), "path": str(args.export_audit)}, indent=2))
        return 0

    if args.confirm_pilot_audit:
        if args.audit_file is None:
            raise SystemExit("--confirm-pilot-audit requires --audit-file <path>")
        validate_isolated_jobs_db_path(db_path, isolated_root=isolated_root)
        confirmed = confirm_pilot_audit(
            state_dir,
            db_path,
            reports_dir,
            boards_path,
            policies_path,
            audit_records_path=args.audit_file,
        )
        print(json.dumps(confirmed, indent=2))
        return 0

    if args.pilot_status:
        scorecard = evaluate_pilot_gates(db_path, state_dir, reports_dir, boards_path, policies_path)
        print(scorecard.model_dump_json(indent=2))
        return 0

    report = run_published_jobs(
        db_path,
        boards_path,
        live=args.live,
        max_verifications=args.max_verifications,
        evaluate_adzuna=args.evaluate_adzuna,
        policies_path=policies_path,
        state_dir=state_dir,
        reports_dir=reports_dir,
        isolated_root=isolated_root,
        skip_workable=args.skip_workable,
        geocode_street_addresses=args.geocode_street_addresses or cfg.published_jobs.geocode_street_addresses,
        origin_label=cfg.published_jobs.origin_label,
        recheck_interval_hours=args.recheck_hours,
        force_recheck=args.force_recheck,
    )
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
