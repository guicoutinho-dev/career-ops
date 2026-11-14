"""Only scan ATS boards linked from a recorded employer career page with independent provenance."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import yaml

from hub.discovery.ats_bridge import AtsBridge
from hub.discovery.published import PublishedListing
from hub.discovery.website import _public_url


_BOARD_HOSTS = {
    "greenhouse": {"boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"},
    "lever": {"jobs.lever.co", "jobs.eu.lever.co"},
    "ashby": {"jobs.ashbyhq.com"},
}

_ALLOWED_DISCOVERY_ORIGINS = {
    "employer_careers_page",
    "employer_direct_link",
    "operator_verified_careers",
}

_FORBIDDEN_PROVENANCE_TOKENS = (
    "riley-dorrington",
    "job-board-aggregator",
    "cc by-nc",
    "cc-by-nc",
    "adzuna",
)

_SECOND_LEVEL_AU_LIKE = {"com", "net", "org", "edu", "gov", "asn", "id"}


@dataclass
class OfficialBoard:
    company: str
    board_url: str
    referrer_url: str
    checked_at: str
    provider_id: str
    discovery_origin: str | None = None
    evidence_ref: str | None = None
    verified_at: str | None = None
    validated_at: float | None = field(default=None, init=False)

    @property
    def slug(self) -> str:
        return board_slug(self.board_url)

    @property
    def policy_source_id(self) -> str:
        return f"ats:{self.provider_id}:{self.slug}"


@dataclass(frozen=True)
class BoardAuditResult:
    company: str
    provider_id: str
    slug: str
    board_url: str
    referrer_url: str
    discovery_origin: str
    evidence_ref: str
    verified_at: str
    valid: bool
    reason: str


class _Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.extend(value for key, value in attrs if key == "href" and value)


def board_slug(board_url: str) -> str:
    """Extract normalized board slug from an ATS board URL."""
    parts = [part for part in urlsplit(board_url).path.split("/") if part]
    return parts[0].strip().lower() if parts else ""


def _registrable_host(hostname: str | None) -> str:
    if not hostname:
        return ""
    labels = [label for label in hostname.lower().rstrip(".").split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SECOND_LEVEL_AU_LIKE:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _has_forbidden_provenance(text: str | None) -> bool:
    lowered = (text or "").lower()
    return any(token in lowered for token in _FORBIDDEN_PROVENANCE_TOKENS)


def validate_board_provenance(entry: dict | OfficialBoard) -> tuple[bool, str]:
    """Validate that a board entry carries non-circular independent employer provenance."""
    if isinstance(entry, OfficialBoard):
        origin = (entry.discovery_origin or "").strip()
        evidence_ref = (entry.evidence_ref or "").strip()
        verified_at = (entry.verified_at or "").strip()
        board_url = entry.board_url.strip()
        referrer_url = entry.referrer_url.strip()
    else:
        origin = str(entry.get("discovery_origin") or "").strip()
        evidence_ref = str(entry.get("evidence_ref") or "").strip()
        verified_at = str(entry.get("verified_at") or "").strip()
        board_url = str(entry.get("board_url") or "").strip()
        referrer_url = str(entry.get("referrer_url") or "").strip()

    if not origin or origin not in _ALLOWED_DISCOVERY_ORIGINS:
        return False, "invalid_or_missing_provenance_origin"
    if not evidence_ref or _has_forbidden_provenance(evidence_ref) or _has_forbidden_provenance(referrer_url):
        return False, "forbidden_or_missing_provenance_evidence"
    if not verified_at:
        return False, "missing_provenance_verified_at"
    try:
        datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
    except ValueError:
        return False, "invalid_provenance_verified_at"
    if board_url and referrer_url and board_url.rstrip("/") == referrer_url.rstrip("/"):
        return False, "circular_referrer_url"
    return True, "ok"


def _valid_seed(board: OfficialBoard) -> bool:
    if not all(isinstance(value, str) for value in (board.company, board.board_url, board.referrer_url, board.provider_id)):
        return False
    if _has_forbidden_provenance(board.referrer_url) or _has_forbidden_provenance(board.evidence_ref):
        return False
    try:
        target = urlsplit(board.board_url)
        referrer = urlsplit(board.referrer_url)
        return bool(
            board.company.strip()
            and board.provider_id in _BOARD_HOSTS
            and target.scheme == referrer.scheme == "https"
            and target.hostname in _BOARD_HOSTS[board.provider_id]
            and target.path.strip("/")
            and target.port in {None, 443}
            and referrer.hostname
            and referrer.hostname != target.hostname
            and referrer.port in {None, 443}
            and not target.username
            and not target.password
            and not referrer.username
            and not referrer.password
        )
    except ValueError:
        return False


def load_official_boards(path: Path) -> list[OfficialBoard]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ValueError("Invalid official board manifest") from exc
    if not isinstance(data, dict) or not isinstance(data.get("boards"), list):
        raise ValueError("Official board manifest requires boards list")
    boards = []
    for raw in data["boards"]:
        if not isinstance(raw, dict):
            raise ValueError("Invalid official board entry")
        try:
            board = OfficialBoard(
                company=raw["company"],
                board_url=raw["board_url"],
                referrer_url=raw["referrer_url"],
                checked_at=raw["checked_at"],
                provider_id=raw["provider_id"],
                discovery_origin=raw.get("discovery_origin"),
                evidence_ref=raw.get("evidence_ref"),
                verified_at=raw.get("verified_at"),
            )
            date.fromisoformat(board.checked_at)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Official board requires company, board URL, referrer URL, checked_at and provider ID") from exc
        if not _valid_seed(board):
            raise ValueError("Invalid official board URL or referrer")
        boards.append(board)
    return boards


_GREENHOUSE_EQUIV_HOSTS = (
    {"boards.greenhouse.io", "job-boards.greenhouse.io"},
    {"boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"},
)


def _equivalent_board_host(link_host: str | None, board_host: str | None) -> bool:
    if not link_host or not board_host:
        return False
    if link_host == board_host:
        return True
    return any(link_host in group and board_host in group for group in _GREENHOUSE_EQUIV_HOSTS)


def _links_to_board(href: str, base: str, board_url: str) -> bool:
    link = urlsplit(urljoin(base, href))
    board = urlsplit(board_url)
    path = board.path.rstrip("/")
    return bool(
        link.scheme == "https"
        and _equivalent_board_host(link.hostname, board.hostname)
        and (link.path.rstrip("/") == path or link.path.startswith(path + "/"))
        and (not board.query or link.query == board.query)
    )


def validate_official_board(board: OfficialBoard, client: httpx.Client) -> bool:
    board.validated_at = None
    if not _valid_seed(board) or not _public_url(board.referrer_url):
        return False
    current_url = board.referrer_url
    redirects = 0
    chunks: list[bytes] = []
    while True:
        try:
            with client.stream("GET", current_url, follow_redirects=False, timeout=8.0) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location or redirects >= 2:
                        return False
                    next_url = urljoin(current_url, location)
                    cur_host = urlsplit(current_url).hostname
                    next_parts = urlsplit(next_url)
                    if (
                        next_parts.scheme != "https"
                        or not _public_url(next_url)
                        or _registrable_host(cur_host) != _registrable_host(next_parts.hostname)
                    ):
                        return False
                    current_url = next_url
                    redirects += 1
                    continue
                if response.status_code != 200 or "html" not in response.headers.get("content-type", "").lower():
                    return False
                total = 0
                chunks = []
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > 524288:
                        return False
                    chunks.append(chunk)
                break
        except httpx.HTTPError:
            return False

    parser = _Links()
    parser.feed(b"".join(chunks).decode("utf-8", errors="replace"))
    if any(_links_to_board(href, current_url, board.board_url) for href in parser.hrefs):
        board.validated_at = datetime.now(timezone.utc).timestamp()
        return True
    return False


def audit_official_boards_manifest(path: Path, client: httpx.Client) -> list[BoardAuditResult]:
    """Audit each manifest board for bridge provider support, provenance, and HTTP link integrity."""
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    raw_boards = data.get("boards") if isinstance(data, dict) else None
    if not isinstance(raw_boards, list):
        return []

    results: list[BoardAuditResult] = []
    for raw in raw_boards:
        if not isinstance(raw, dict):
            results.append(
                BoardAuditResult("", "", "", "", "", "", "", "", False, "invalid_entry_type")
            )
            continue
        company = str(raw.get("company") or "").strip()
        provider_id = str(raw.get("provider_id") or "").strip()
        board_url = str(raw.get("board_url") or "").strip()
        referrer_url = str(raw.get("referrer_url") or "").strip()
        checked_at = str(raw.get("checked_at") or "").strip()
        origin = str(raw.get("discovery_origin") or "").strip()
        evidence_ref = str(raw.get("evidence_ref") or "").strip()
        verified_at = str(raw.get("verified_at") or "").strip()
        slug = board_slug(board_url)

        if provider_id not in _BOARD_HOSTS:
            results.append(
                BoardAuditResult(company, provider_id, slug, board_url, referrer_url, origin, evidence_ref, verified_at, False, "unsupported_provider")
            )
            continue
        prov_ok, prov_reason = validate_board_provenance(raw)
        if not prov_ok:
            results.append(
                BoardAuditResult(company, provider_id, slug, board_url, referrer_url, origin, evidence_ref, verified_at, False, prov_reason)
            )
            continue
        try:
            date.fromisoformat(checked_at)
            board = OfficialBoard(
                company=company,
                board_url=board_url,
                referrer_url=referrer_url,
                checked_at=checked_at,
                provider_id=provider_id,
                discovery_origin=origin,
                evidence_ref=evidence_ref,
                verified_at=verified_at,
            )
        except ValueError:
            results.append(
                BoardAuditResult(company, provider_id, slug, board_url, referrer_url, origin, evidence_ref, verified_at, False, "invalid_checked_at")
            )
            continue
        if not _valid_seed(board):
            results.append(
                BoardAuditResult(company, provider_id, slug, board_url, referrer_url, origin, evidence_ref, verified_at, False, "invalid_board_or_referrer_url")
            )
            continue
        if not validate_official_board(board, client):
            results.append(
                BoardAuditResult(company, provider_id, slug, board_url, referrer_url, origin, evidence_ref, verified_at, False, "referrer_link_verification_failed")
            )
            continue
        results.append(
            BoardAuditResult(company, provider_id, slug, board_url, referrer_url, origin, evidence_ref, verified_at, True, "ok")
        )
    return results


def fetch_board_listings(
    board: OfficialBoard,
    bridge: AtsBridge,
    *,
    include_board_slug: bool = False,
) -> list[PublishedListing]:
    if board.validated_at is None:
        raise ValueError("Official board must be validated first")
    detected = bridge.detect([board.board_url], company=board.company)
    if not any(item.url == board.board_url and item.supported and item.provider_id == board.provider_id for item in detected):
        raise ValueError("Official board provider mismatch")
    result = bridge.fetch(board.board_url, company=board.company, provider_id=board.provider_id)
    if not result.ok:
        raise RuntimeError(result.error or "ATS board fetch failed")
    source_id = board.policy_source_id if include_board_slug else f"ats:{board.provider_id}"
    listings = []
    for job in result.jobs:
        link = urlsplit(job.url)
        if link.scheme != "https" or link.hostname not in _BOARD_HOSTS[board.provider_id]:
            continue
        try:
            published_at = datetime.fromtimestamp(job.posted_at / (1000 if job.posted_at > 100_000_000_000 else 1), timezone.utc) if job.posted_at else None
        except (OverflowError, OSError, ValueError):
            published_at = None
        listings.append(
            PublishedListing(
                source_id=source_id,
                external_id=job.external_id,
                source_url=job.url,
                title=job.title,
                company=job.company,
                location=job.location or None,
                published_at=published_at,
                employment_type=None,
                description=job.description,
                lat=None,
                lng=None,
            )
        )
    return listings
