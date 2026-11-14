from pathlib import Path

import httpx
import pytest

from hub.discovery.ats_bridge import AtsFetchResult, AtsJob, AtsMatch
from hub.discovery.published_ats import (
    OfficialBoard,
    audit_official_boards_manifest,
    board_slug,
    fetch_board_listings,
    load_official_boards,
    validate_board_provenance,
    validate_official_board,
)


BOARD = "https://job-boards.greenhouse.io/acmecafe"
REFERRER = "https://example.com/careers"


def board(**overrides) -> OfficialBoard:
    values = dict(company="Acme Cafe", board_url=BOARD, referrer_url=REFERRER,
                  checked_at="2026-11-14", provider_id="greenhouse")
    values.update(overrides)
    return OfficialBoard(**values)


def client_for(html: str, *, status: int = 200) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(
        status, text=html, headers={"content-type": "text/html"})))


def test_manifest_requires_first_party_referrer_and_supported_provider(tmp_path: Path):
    path = tmp_path / "boards.yml"
    path.write_text(f"""boards:
  - company: Acme Cafe
    board_url: {BOARD}
    referrer_url: {REFERRER}
    checked_at: '2026-11-14'
    provider_id: greenhouse
""", encoding="utf-8")
    loaded = load_official_boards(path)
    assert len(loaded) == 1
    assert loaded[0].board_url == BOARD
    assert loaded[0].validated_at is None
    path.write_text(f"boards:\n  - company: Acme Cafe\n    board_url: {BOARD}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="referrer"):
        load_official_boards(path)
    path.write_text(f"boards:\n  - company: 42\n    board_url: {BOARD}\n    referrer_url: {REFERRER}\n    checked_at: '2026-11-14'\n    provider_id: greenhouse\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid"):
        load_official_boards(path)
    path.write_text("boards: [invalid", encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        load_official_boards(path)


def test_official_career_page_must_link_to_exact_board_path():
    seed = board()
    with client_for(f'<a href="{BOARD}">Jobs</a>') as client:
        assert validate_official_board(seed, client) is True
    assert seed.validated_at is not None

    unrelated = board()
    with client_for('<a href="https://job-boards.greenhouse.io/othercorp">Jobs</a>') as client:
        assert validate_official_board(unrelated, client) is False
    assert unrelated.validated_at is None


def test_private_referrer_redirect_and_rate_limit_are_rejected_without_following():
    with client_for("ignored") as client:
        assert validate_official_board(board(referrer_url="http://127.0.0.1/careers"), client) is False
    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(
        302, headers={"location": "https://other.example/jobs"}))) as client:
        assert validate_official_board(board(), client) is False
    with client_for("", status=429) as client:
        assert validate_official_board(board(), client) is False


def test_bridge_publication_preserves_native_id_url_location_and_missing_date():
    seed = board()
    with client_for(f'<a href="{BOARD}">Apply</a>') as client:
        assert validate_official_board(seed, client)

    class Bridge:
        def detect(self, urls, *, company):
            assert urls == [BOARD] and company == "Acme Cafe"
            return [AtsMatch(BOARD, "greenhouse", True, None)]

        def fetch(self, url, *, company, provider_id):
            assert (url, company, provider_id) == (BOARD, "Acme Cafe", "greenhouse")
            return AtsFetchResult(True, "greenhouse", [
                AtsJob("Barista", "https://job-boards.greenhouse.io/acmecafe/jobs/101", "Acme Cafe",
                       "Rockdale NSW", None, 1794442800000, "101"),
                AtsJob("Cleaner", "https://job-boards.greenhouse.io/acmecafe/jobs/102", "Acme Cafe",
                       "Mascot NSW", None, None, "102"),
            ], None, 1)

    jobs = fetch_board_listings(seed, Bridge())
    assert [job.external_id for job in jobs] == ["101", "102"]
    assert [job.source_id for job in jobs] == ["ats:greenhouse", "ats:greenhouse"]
    assert jobs[0].source_url == "https://job-boards.greenhouse.io/acmecafe/jobs/101"
    assert jobs[0].location == "Rockdale NSW"
    assert jobs[0].published_at is not None and jobs[0].published_at.year == 2026
    assert jobs[1].published_at is None


def test_unvalidated_board_or_failed_source_cannot_publish():
    class Bridge:
        def detect(self, *_args, **_kwargs):
            return [AtsMatch(BOARD, "greenhouse", True, None)]

        def fetch(self, *_args, **_kwargs):
            return AtsFetchResult(False, "greenhouse", [], "429 Too Many Requests", 1)

    with pytest.raises(ValueError, match="validated"):
        fetch_board_listings(board(), Bridge())
    seed = board()
    with client_for(f'<a href="{BOARD}">Jobs</a>') as client:
        assert validate_official_board(seed, client)
    with pytest.raises(RuntimeError, match="429"):
        fetch_board_listings(seed, Bridge())


def test_bridge_job_link_outside_provider_is_not_exposed():
    seed = board()
    with client_for(f'<a href="{BOARD}">Jobs</a>') as client:
        assert validate_official_board(seed, client)

    class Bridge:
        def detect(self, *_args, **_kwargs):
            return [AtsMatch(BOARD, "greenhouse", True, None)]

        def fetch(self, *_args, **_kwargs):
            return AtsFetchResult(True, "greenhouse", [
                AtsJob("Barista", "http://127.0.0.1/jobs/101", "Acme Cafe", "Rockdale NSW", None, None, "101"),
            ], None, 1)

    assert fetch_board_listings(seed, Bridge()) == []


def test_manifest_requires_independent_provenance_and_rejects_aggregators_or_self_origin(tmp_path: Path):
    valid_entry = {
        "company": "Acme Cafe",
        "board_url": BOARD,
        "referrer_url": "https://cafe.com.au/careers",
        "checked_at": "2026-11-14",
        "provider_id": "greenhouse",
        "discovery_origin": "employer_careers_page",
        "evidence_ref": "Employer footer link on cafe.com.au/careers",
        "verified_at": "2026-11-14T10:00:00+11:00",
    }
    assert validate_board_provenance(valid_entry) == (True, "ok")
    assert board_slug(BOARD) == "acmecafe"

    missing_prov = dict(valid_entry)
    missing_prov.pop("discovery_origin")
    ok, reason = validate_board_provenance(missing_prov)
    assert ok is False and "provenance" in reason

    for bad_origin in ("adzuna", "riley_dorrington", "job_board_aggregator", "cc_by_nc", "self_inferred"):
        bad = dict(valid_entry, discovery_origin=bad_origin)
        ok, reason = validate_board_provenance(bad)
        assert ok is False

    circular = dict(valid_entry, referrer_url=BOARD)
    ok, _ = validate_board_provenance(circular)
    assert ok is False

    manifest = tmp_path / "published_boards.yml"
    manifest.write_text(
        "boards:\n"
        "  - company: Acme Cafe\n"
        f"    board_url: {BOARD}\n"
        "    referrer_url: https://cafe.com.au/careers\n"
        "    checked_at: '2026-11-14'\n"
        "    provider_id: greenhouse\n"
        "    discovery_origin: adzuna\n"
        "    evidence_ref: scraped from aggregator\n"
        "    verified_at: '2026-11-14T10:00:00Z'\n",
        encoding="utf-8",
    )
    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, text=f'<a href="{BOARD}">Jobs</a>', headers={"content-type": "text/html"}))) as client:
        results = audit_official_boards_manifest(manifest, client)
    assert len(results) == 1
    assert results[0].valid is False
    assert "provenance" in results[0].reason or "aggregator" in results[0].reason


def test_manifest_rejects_cc_by_nc_unverified_providers_and_cross_host_redirects(tmp_path: Path):
    manifest = tmp_path / "published_boards.yml"
    manifest.write_text(
        "boards:\n"
        "  - company: Cafe Redirect OK\n"
        f"    board_url: {BOARD}\n"
        "    referrer_url: https://cafe.com.au/careers\n"
        "    checked_at: '2026-11-14'\n"
        "    provider_id: greenhouse\n"
        "    discovery_origin: employer_careers_page\n"
        "    evidence_ref: Verified on cafe.com.au/careers\n"
        "    verified_at: '2026-11-14T10:00:00Z'\n"
        "  - company: Aggregator Seed\n"
        "    board_url: https://jobs.lever.co/badcafe\n"
        "    referrer_url: https://github.com/Riley-Dorrington/job-board-aggregator\n"
        "    checked_at: '2026-11-14'\n"
        "    provider_id: lever\n"
        "    discovery_origin: employer_careers_page\n"
        "    evidence_ref: Riley-Dorrington CC BY-NC 4.0\n"
        "    verified_at: '2026-11-14T10:00:00Z'\n"
        "  - company: Unsupported Workday\n"
        "    board_url: https://acme.wd3.myworkdayjobs.com/careers\n"
        "    referrer_url: https://acme.com.au/careers\n"
        "    checked_at: '2026-11-14'\n"
        "    provider_id: workday\n"
        "    discovery_origin: employer_careers_page\n"
        "    evidence_ref: acme.com.au\n"
        "    verified_at: '2026-11-14T10:00:00Z'\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://cafe.com.au/careers":
            return httpx.Response(302, headers={"location": "https://www.cafe.com.au/careers/"})
        if str(request.url) == "https://www.cafe.com.au/careers/":
            return httpx.Response(200, text=f'<a href="{BOARD}">Jobs</a>', headers={"content-type": "text/html"})
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        results = audit_official_boards_manifest(manifest, client)

    assert len(results) == 3
    assert results[0].valid is True and results[0].slug == "acmecafe"
    assert results[1].valid is False
    assert results[2].valid is False and "provider" in results[2].reason


def test_official_board_accepts_greenhouse_redirect_and_theiconic_example_seed():
    from hub.discovery.source_policy import load_source_policies, resolve_board_policy

    root = Path(__file__).resolve().parent.parent
    boards = load_official_boards(root / "config" / "published_boards.example.yml")
    iconic = [b for b in boards if b.slug == "theiconic"]
    assert len(iconic) == 1
    seed = iconic[0]
    assert seed.provider_id == "greenhouse"
    assert seed.board_url == "https://job-boards.greenhouse.io/theiconic"
    assert seed.referrer_url == "https://www.theiconic.com.au/playbook/careers"
    assert validate_board_provenance(seed)[0] is True
    assert not any("hometime" in b.company.lower() or "hellofresh" in b.company.lower() for b in boards)

    # Referrer page links to boards.greenhouse.io/theiconic, which canonicalizes to job-boards.greenhouse.io/theiconic
    with client_for('<a href="https://boards.greenhouse.io/theiconic">Open Roles</a>') as client:
        assert validate_official_board(seed, client) is True

    # Must stay fail-closed (publish_allowed=False) until individual policy & audit pass
    policies = load_source_policies(root / "config" / "source_policies.example.yml")
    assert resolve_board_policy(policies, "greenhouse", "theiconic").publish_allowed is False
