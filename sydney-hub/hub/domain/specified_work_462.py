"""Versioned Home Affairs Subclass 462 specified-work ruleset. Pure functions, no I/O."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal


RULES_VERSION = "home-affairs-462-2026-11"
DISCLAIMER = (
    "Indicative ruleset check only (Home Affairs Subclass 462) — never guarantees visa credit. "
    "Confirm exact postcode, employer ABN, genuine paid award wages, and payslips at immi.homeaffairs.gov.au."
)

_POSTCODE_RE = re.compile(r"\b(\d{4})\b")

_SYDNEY_METRO_SUBURBS: dict[str, str] = {
    "sydney": "2000", "sydney cbd": "2000", "mascot": "2020", "rockdale": "2216",
    "kogarah": "2217", "hurstville": "2220", "wolli creek": "2205", "arncliffe": "2205",
    "newtown": "2042", "bondi": "2026", "parramatta": "2150", "chatswood": "2067",
    "alexandria": "2015", "rosebery": "2018", "zetland": "2017", "waterloo": "2017",
    "surry hills": "2010", "redfern": "2016", "marrickville": "2204", "manly": "2095",
    "coogee": "2034", "maroubra": "2035", "randwick": "2031", "kingsford": "2032",
    "burwood": "2134", "strathfield": "2135", "ashfield": "2131", "bankstown": "2200",
    "cronulla": "2230", "sutherland": "2232", "brighton-le-sands": "2216",
    "kogarah bay": "2217", "banksia": "2216", "carlton": "2218", "allawah": "2218",
    "botany": "2019", "banksmeadow": "2019", "eastgardens": "2036", "tempe": "2044",
}

_OTHER_METRO_SUBURBS: set[str] = {
    "melbourne", "brisbane", "perth", "adelaide", "canberra", "gold coast", "newcastle", "wollongong",
}

_NORTHERN_OR_REMOTE_RANGES: tuple[tuple[int, int], ...] = (
    (800, 899),    # NT (all)
    (2831, 2831), (2836, 2836), (2839, 2839), (2878, 2880), (2898, 2899),  # NSW remote
    (4406, 4406), (4416, 4416), (4470, 4498), (4680, 4680), (4700, 4899),  # QLD northern/remote
    (5600, 5734),  # SA remote/very remote
    (6430, 6799),  # WA northern/remote
    (7215, 7215),  # TAS designated remote postcode
)

_REGIONAL_CULTIVATION_RANGES: tuple[tuple[int, int], ...] = (
    (800, 899),    # NT
    (2250, 2251), (2256, 2263), (2311, 2490), (2536, 2551), (2575, 2739), (2787, 2899),  # NSW regional
    (3139, 3139), (3211, 3334), (3340, 3424), (3430, 3799), (3812, 3996),  # VIC regional
    (4124, 4125), (4133, 4133), (4211, 4211), (4270, 4272), (4275, 4275),
    (4280, 4280), (4285, 4285), (4287, 4287), (4307, 4499), (4510, 4510),
    (4512, 4512), (4515, 4899),  # QLD regional
    (5200, 5734),  # SA regional
    (6041, 6044), (6083, 6084), (6121, 6126), (6200, 6799),  # WA regional
    (7000, 7999),  # TAS
)

_METRO_POSTCODE_RANGES: tuple[tuple[int, int], ...] = (
    (2000, 2249),  # Greater Sydney
    (2500, 2534),  # Wollongong metro
    (2280, 2310),  # Newcastle metro
    (3000, 3138), (3140, 3210),  # Melbourne metro
    (4000, 4123),  # Brisbane metro
    (5000, 5119),  # Adelaide metro
    (6000, 6040), (6050, 6082), (6090, 6120),  # Perth metro
)

_INDUSTRY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "plant_animal_cultivation",
        re.compile(
            r"\b(?:fruit\s+pick(?:er|ing)|harvest(?:er|ing)?|farm\s+hands?|vineyard|prun(?:er|ing)|"
            r"horticulture|orchard|packing\s+shed|packhouse|livestock|dairy|shearing|nursery|crop)\b",
            re.I,
        ),
    ),
    (
        "tourism_hospitality",
        re.compile(
            r"\b(?:baristas?|bartenders?|waitstaff|waiters?|waitresses|kitchen\s*hands?|cooks?|chefs?|"
            r"housekeep(?:er|ers|ing)|room\s+attendants?|hotel|motel|resort|hostel|tour\s+guide|"
            r"hospitality|all[- ]?rounder|cafe|pub|bistro)\b",
            re.I,
        ),
    ),
    (
        "construction",
        re.compile(r"\b(?:construction|labourers?|laborers?|site\s+hands?|carpenter|scaffolder|traffic\s+control)\b", re.I),
    ),
    (
        "fishing_pearling",
        re.compile(r"\b(?:fishing\s+crew|deckhand|aquaculture|pearling|trawler)\b", re.I),
    ),
    (
        "tree_farming_felling",
        re.compile(r"\b(?:forestry|tree\s+plant(?:er|ing)|tree\s+felling|silviculture)\b", re.I),
    ),
)

_NON_SPECIFIED_INDUSTRY_RE = re.compile(
    r"\b(?:software\s+engineer|developer|sap\b|abap\b|accountant|solicitor|retail\s+assistant|"
    r"warehouse\s+(?:picker|packer|operative)|marketing|it\s+support|corporate)\b",
    re.I,
)


@dataclass(frozen=True)
class SpecifiedWork462Assessment:
    status: Literal["eligible", "ineligible", "needs_review"]
    rules_version: str
    matched_industry: str | None
    postcode: str | None
    region_eligible: bool | None
    reasons: list[str] = field(default_factory=list)
    missing_facts: list[str] = field(default_factory=list)
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "rules_version": self.rules_version,
            "matched_industry": self.matched_industry,
            "postcode": self.postcode,
            "region_eligible": self.region_eligible,
            "reasons": list(self.reasons),
            "missing_facts": list(self.missing_facts),
            "disclaimer": self.disclaimer,
        }


def _in_ranges(code: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(low <= code <= high for low, high in ranges)


def _extract_postcode(opportunity: dict[str, Any]) -> tuple[str | None, bool]:
    """Return (postcode_str, is_explicit_metro_without_postcode)."""
    loc_text = " ".join(
        str(opportunity.get(k) or "") for k in ("location", "suburb", "title", "description")
    )
    for match in _POSTCODE_RE.finditer(loc_text):
        num = int(match.group(1))
        if 800 <= num <= 7999 and not (1900 <= num <= 1999):
            # Avoid matching calendar years like 2025/2026 inside narrative descriptions unless in location/suburb
            in_location = match.group(1) in str(opportunity.get("location") or "")
            if in_location or not (2020 <= num <= 2035 and match.group(1) not in str(opportunity.get("location") or "")):
                return match.group(1), False

    suburb_norm = str(opportunity.get("suburb") or "").strip().casefold()
    if suburb_norm in _SYDNEY_METRO_SUBURBS:
        return _SYDNEY_METRO_SUBURBS[suburb_norm], True
    loc_norm = str(opportunity.get("location") or "").casefold()
    for name, code in _SYDNEY_METRO_SUBURBS.items():
        if re.search(rf"\b{re.escape(name)}\b", loc_norm):
            return code, True
    for metro in _OTHER_METRO_SUBURBS:
        if re.search(rf"\b{re.escape(metro)}\b", loc_norm):
            return None, True
    return None, False


def _classify_industry(opportunity: dict[str, Any]) -> tuple[str | None, bool]:
    title = str(opportunity.get("title") or "").strip()
    desc = str(opportunity.get("description") or "").strip()
    text = f"{title} {desc[:400]}".strip()
    if not text:
        return None, False
    if title and _NON_SPECIFIED_INDUSTRY_RE.search(title):
        return None, True
    for industry, pattern in _INDUSTRY_PATTERNS:
        if pattern.search(text):
            return industry, False
    return None, False


def classify_specified_work_462(opportunity: dict[str, Any]) -> SpecifiedWork462Assessment:
    """Classify an opportunity for Subclass 462 88-day specified work without guessing missing facts."""
    postcode, explicit_metro = _extract_postcode(opportunity)
    industry, explicit_non_specified = _classify_industry(opportunity)
    verification = opportunity.get("verification_state") or "unverified_job_post"

    reasons: list[str] = []
    missing: list[str] = []

    if explicit_non_specified:
        reasons.append("Role/industry is explicitly outside Home Affairs Subclass 462 specified-work categories")
        return SpecifiedWork462Assessment(
            status="ineligible",
            rules_version=RULES_VERSION,
            matched_industry=None,
            postcode=postcode,
            region_eligible=False if explicit_metro else None,
            reasons=reasons,
            missing_facts=missing,
        )

    code_num = int(postcode) if postcode and postcode.isdigit() else None
    if explicit_metro or (code_num is not None and _in_ranges(code_num, _METRO_POSTCODE_RANGES)):
        label = f"postcode {postcode}" if postcode else "metropolitan area"
        reasons.append(f"Location ({label}) is Greater Sydney / metropolitan Australia, which is ineligible for 462 specified work")
        return SpecifiedWork462Assessment(
            status="ineligible",
            rules_version=RULES_VERSION,
            matched_industry=industry,
            postcode=postcode,
            region_eligible=False,
            reasons=reasons,
            missing_facts=missing,
        )

    if code_num is not None and industry is not None:
        if industry in {"tourism_hospitality", "fishing_pearling", "tree_farming_felling"}:
            area_ok = _in_ranges(code_num, _NORTHERN_OR_REMOTE_RANGES)
            area_label = "Northern or Remote/Very Remote Australia"
        else:
            area_ok = _in_ranges(code_num, _REGIONAL_CULTIVATION_RANGES)
            area_label = "designated Regional Australia"

        if not area_ok:
            reasons.append(
                f"Postcode {postcode} is not within eligible {area_label} areas for '{industry}'"
            )
            return SpecifiedWork462Assessment(
                status="ineligible",
                rules_version=RULES_VERSION,
                matched_industry=industry,
                postcode=postcode,
                region_eligible=False,
                reasons=reasons,
                missing_facts=missing,
            )

        reasons.append(f"Postcode {postcode} matches {area_label} for '{industry}'")
        region_eligible: bool | None = True
    else:
        region_eligible = None
        if postcode is None:
            missing.append("Exact Australian postcode is missing")
        if industry is None:
            missing.append("Specified-work industry/duties could not be confirmed from title or description")

    if verification != "verified_job":
        missing.append("Unverified source (employer lead or community post) — confirm open role and employer ABN")
    if not opportunity.get("published_at"):
        missing.append("Publication/work period date not confirmed")
    pay = opportunity.get("pay_hourly_min")
    if isinstance(pay, (int, float)) and pay < 24.10:
        missing.append(f"Disclosed pay (${pay:g}/h) is below national minimum wage — unpaid/below-award work does not qualify")

    if missing:
        return SpecifiedWork462Assessment(
            status="needs_review",
            rules_version=RULES_VERSION,
            matched_industry=industry,
            postcode=postcode,
            region_eligible=region_eligible,
            reasons=reasons,
            missing_facts=missing,
        )

    return SpecifiedWork462Assessment(
        status="eligible",
        rules_version=RULES_VERSION,
        matched_industry=industry,
        postcode=postcode,
        region_eligible=True,
        reasons=reasons,
        missing_facts=[],
    )
