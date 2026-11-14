"""Strict explicit-suburb location disambiguator and operational hospitality/cleaning role filter."""
from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from typing import Callable, Literal
from urllib.parse import urlsplit

import httpx

from hub.discovery.ats_bridge import AtsJob
from hub.discovery.website import _MAX_BYTES, _public_url

_PILOT_SUBURB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Rockdale", re.compile(r"\b(?:rockdale|2216)\b", re.I)),
    ("Kogarah", re.compile(r"\b(?:kogarah|2217)\b", re.I)),
    ("Hurstville", re.compile(r"\b(?:hurstville|2220)\b", re.I)),
    ("Mascot", re.compile(r"\b(?:mascot|2020)\b", re.I)),
)

# In narrative text or URL slugs, bare 4-digit postcodes (especially 2020) match calendar years or numeric IDs.
_NARRATIVE_SUBURB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Rockdale", re.compile(r"\b(?:rockdale|(?:NSW|postcode|suburb|location)\s*[:\-]?\s*2216)\b", re.I)),
    ("Kogarah", re.compile(r"\b(?:kogarah|(?:NSW|postcode|suburb|location)\s*[:\-]?\s*2217)\b", re.I)),
    ("Hurstville", re.compile(r"\b(?:hurstville|(?:NSW|postcode|suburb|location)\s*[:\-]?\s*2220)\b", re.I)),
    ("Mascot", re.compile(r"\b(?:mascot|(?:NSW|postcode|suburb|location)\s*[:\-]?\s*2020)\b", re.I)),
)

_GENERIC_LOCATION = re.compile(
    r"^(?:greater\s+sydney|sydney(?:\s+metro)?|sydney\s*,?\s*nsw|nsw|new\s+south\s+wales|"
    r"australia|sydney\s*,?\s*australia|nsw\s*,?\s*australia)$",
    re.I,
)

_CORPORATE_TITLE = re.compile(
    r"\b(?:accountant|accounting|finance|financial|software\s+engineer|developer|devops|"
    r"it\s+support|helpdesk|human\s+resources|hr\s+(?:manager|advisor|partner|coordinator)|"
    r"people\s+(?:&|and)\s+culture|legal\s+counsel|solicitor|marketing|payroll|actuary|"
    r"treasury|cybersecurity|architect|data\s+scientist|analyst|"
    r"sales\s+(?:representative|executive|manager)|account\s+(?:executive|manager)|"
    r"business\s+development|recruit(?:er|ment)|office\s+(?:manager|coordinator|admin)|"
    r"executive\s+assistant)\b",
    re.I,
)

_GENERIC_OPERATIONAL_TITLE = re.compile(
    r"\b(?:casual(?:\s+(?:staff|team|role|position|hand))?|team\s+member|crew\s+member|"
    r"all[- ]?rounder|venue\s+(?:staff|team)|hospitality\s+(?:staff|team)|"
    r"general\s+hand|staff\s+wanted|now\s+hiring)\b",
    re.I,
)

_ROLE_BUCKETS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("barista", re.compile(r"\bbaristas?\b", re.I)),
    ("bartender", re.compile(r"\b(?:bartenders?|bar\s+(?:staff|attendant|tender))\b", re.I)),
    ("housekeeping", re.compile(r"\b(?:housekeep(?:er|ers|ing)|room\s+attendants?)\b", re.I)),
    ("cleaner", re.compile(r"\b(?:commercial\s+)?clean(?:er|ers|ing)\b|\blaundry\s+attendant\b", re.I)),
    ("kitchen", re.compile(r"\b(?:kitchen\s+hands?|cooks?|chefs?|dishwashers?|stewards?|bakers?|pastry)\b", re.I)),
    ("front_of_house", re.compile(r"\b(?:waitstaff|waiters?|waitresses|front\s+(?:desk|office)|guest\s+service|concierge|porters?|night\s+audit|receptionists?)\b", re.I)),
    ("hospitality", re.compile(r"\b(?:hospitality|food\s*(?:&|and)\s*beverage|f&b|cafe\s+all[- ]rounder|all[- ]rounder|venue\s+(?:staff|attendant|manager)|duty\s+manager|catering|bistro|pub\s+staff)\b", re.I)),
)

_TAG_STRIP = re.compile(r"<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)>|<[^>]+>", re.I | re.S)
_NON_PILOT_PLACE = re.compile(
    r"\b(?:melbourne|brisbane|perth|adelaide|canberra|hobart|darwin|sydney\s+cbd|parramatta|bondi|chatswood|newtown|surry\s+hills|manly)\b",
    re.I,
)


@dataclass(frozen=True)
class FilteredAtsJob:
    job: AtsJob
    matched_suburb: str
    role_bucket: str
    match_basis: Literal["explicit_job_location", "ats_detail_location"]
    location_evidence: str


@dataclass(frozen=True)
class AtsFilterSummary:
    kept: list[FilteredAtsJob]
    dropped_outside_suburbs: int
    dropped_ambiguous_location: int
    dropped_non_target_role: int
    detail_requests: int


def _strip_html(raw: str) -> str:
    text = _TAG_STRIP.sub(" ", raw or "")
    text = html_lib.unescape(text)
    return " ".join(text.split())


def _match_pilot_suburb(
    text: str, *, preferred: str | None = None, narrative: bool = False
) -> tuple[str, str] | None:
    if not text:
        return None
    patterns = _NARRATIVE_SUBURB_PATTERNS if narrative else _PILOT_SUBURB_PATTERNS
    hits: list[tuple[str, str]] = []
    for suburb, pattern in patterns:
        m = pattern.search(text)
        if m:
            start = max(0, m.start() - 30)
            end = min(len(text), m.end() + 30)
            hits.append((suburb, text[start:end].strip()))
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    if preferred:
        for suburb, snippet in hits:
            if suburb == preferred:
                return suburb, snippet
    return hits[0]


def classify_role_bucket(title: str, description: str | None = None) -> str | None:
    if not title or _CORPORATE_TITLE.search(title):
        return None
    for bucket, pattern in _ROLE_BUCKETS:
        if pattern.search(title):
            return bucket
    if description and _GENERIC_OPERATIONAL_TITLE.search(title):
        for bucket, pattern in _ROLE_BUCKETS:
            if pattern.search(description[:600]):
                return bucket
    return None


def _host_matches(url: str, allowlisted_host: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")
    allowed = (allowlisted_host or "").lower().removeprefix("www.")
    return bool(host and allowed and (host == allowed or host.endswith(f".{allowed}")))


def fetch_ats_detail_html(url: str, *, allowlisted_host: str, client: httpx.Client) -> str | None:
    if not _host_matches(url, allowlisted_host) or not _public_url(url):
        return None
    try:
        with client.stream("GET", url, follow_redirects=False, timeout=8) as response:
            if response.status_code != 200 or "html" not in response.headers.get("content-type", "").lower():
                return None
            chunks, total = [], 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > _MAX_BYTES:
                    return None
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", errors="replace")
    except httpx.HTTPError:
        return None


def filter_ats_jobs(
    jobs: list[AtsJob],
    *,
    expected_suburb: str,
    allowlisted_host: str,
    fetch_detail: Callable[[str], str | None] | None = None,
    max_detail_requests: int = 10,
) -> AtsFilterSummary:
    kept: list[FilteredAtsJob] = []
    dropped_outside = 0
    dropped_ambiguous = 0
    dropped_role = 0
    detail_requests = 0
    bounded_cap = min(max(0, int(max_detail_requests)), 10)

    for job in jobs:
        loc_raw = (job.location or "").strip()
        loc_match = _match_pilot_suburb(loc_raw, preferred=expected_suburb)
        if loc_raw and loc_match is None and not _GENERIC_LOCATION.match(loc_raw):
            dropped_outside += 1
            continue

        role_bucket = classify_role_bucket(job.title, job.description)
        if role_bucket is None:
            dropped_role += 1
            continue

        if loc_match is not None:
            suburb, evidence = loc_match
            kept.append(FilteredAtsJob(job, suburb, role_bucket, "explicit_job_location", evidence))
            continue

        # Secondary list-level proof in title or URL slug (never blindly trust group-wide footer text if ambiguous)
        list_hint = " ".join(part for part in (job.title, job.url) if part)
        hint_match = _match_pilot_suburb(list_hint, preferred=expected_suburb, narrative=True)
        if hint_match is not None:
            suburb, evidence = hint_match
            kept.append(FilteredAtsJob(job, suburb, role_bucket, "explicit_job_location", evidence))
            continue

        if job.description:
            desc_plain = _strip_html(job.description)
            desc_hits = [suburb for suburb, pat in _NARRATIVE_SUBURB_PATTERNS if pat.search(desc_plain)]
            if len(desc_hits) == 1 and not _NON_PILOT_PLACE.search(desc_plain):
                hit = _match_pilot_suburb(desc_plain, preferred=desc_hits[0], narrative=True)
                assert hit is not None
                kept.append(FilteredAtsJob(job, hit[0], role_bucket, "explicit_job_location", hit[1]))
                continue

        if fetch_detail is not None and detail_requests < bounded_cap and _host_matches(job.url, allowlisted_host):
            detail_requests += 1
            detail_raw = fetch_detail(job.url)
            if detail_raw:
                detail_text = _strip_html(detail_raw)
                detail_hits = [
                    (suburb, pat.search(detail_text))
                    for suburb, pat in _NARRATIVE_SUBURB_PATTERNS
                    if pat.search(detail_text)
                ]
                # Require unambiguous target-suburb mention or explicit "Location: <suburb>"
                loc_line = re.search(
                    r"(?:location|suburb|workplace|venue|site)\s*[:\-]\s*([^\n]{3,80})",
                    detail_text,
                    re.I,
                )
                if loc_line:
                    explicit_hit = _match_pilot_suburb(loc_line.group(1), preferred=expected_suburb)
                    if explicit_hit is not None:
                        kept.append(
                            FilteredAtsJob(job, explicit_hit[0], role_bucket, "ats_detail_location", loc_line.group(0).strip())
                        )
                        continue
                if len(detail_hits) == 1:
                    suburb, m = detail_hits[0]
                    assert m is not None
                    start = max(0, m.start() - 30)
                    end = min(len(detail_text), m.end() + 30)
                    kept.append(
                        FilteredAtsJob(job, suburb, role_bucket, "ats_detail_location", detail_text[start:end].strip())
                    )
                    continue

        dropped_ambiguous += 1

    return AtsFilterSummary(
        kept=kept,
        dropped_outside_suburbs=dropped_outside,
        dropped_ambiguous_location=dropped_ambiguous,
        dropped_non_target_role=dropped_role,
        detail_requests=detail_requests,
    )
