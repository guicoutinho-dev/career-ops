"""
Job categorisation and Working Holiday (subclass 462) visa-fit classification.

Honesty rule: the 88-day specified-work requirement depends on BOTH the industry and
the work location postcode (regional area list published by Home Affairs). We only have
the free-text location from the job board, so we can never assert eligibility with
certainty. Output is therefore one of:

- ``88d_likely``   industry keyword matches AND location is not an obvious metro area
- ``88d_verify``   industry keyword matches BUT location is metro/unknown -> check postcode
- ``open_market``  no 88-day industry signal (normal work, counts toward the 6-month employer limit)
"""

import re
from typing import Tuple

CATEGORY_SAP = "SAP Specialist"
CATEGORY_88D = "88 Days Specified Work"
CATEGORY_OFFICE = "Office Support"
CATEGORY_HOSP = "Hospitality / Barista"
CATEGORY_OTHER = "Other"

VISA_88D_LIKELY = "88d_likely"
VISA_88D_VERIFY = "88d_verify"
VISA_OPEN = "open_market"

# Industries that can count as specified work: plant & animal cultivation, mining,
# construction, (tourism/hospitality only in northern/remote areas - treated as verify).
_88D_PATTERNS = [
    r"\bfarm\s*hand\b", r"\bfarmhand\b", r"\bfarm\s+(worker|labourer|laborer|assistant)\b",
    r"\bfruit\s+pick", r"\bharvest", r"\bagricultur", r"\bhorticultur",
    r"\bvineyard\b", r"\borchard\b", r"\bcrop\b", r"\bgrain\b", r"\bcotton\b",
    r"\bstation\s+hand\b", r"\bjackaroo\b", r"\bjillaroo\b", r"\bstockman\b", r"\bdairy\b",
    r"\bpack\s*house\b", r"\bpackhouse\b", r"\bpruning\b", r"\birrigation\b",
    r"\bsolar\s+farm\b", r"\bsolar\s+(installer|labourer|laborer|civil)\b",
    r"\bmeat\s+(worker|process)", r"\babattoir\b", r"\bslaughter",
    r"\bforestry\b", r"\bfishing\b", r"\baquaculture\b",
    r"\bconstruction\s+(labourer|laborer|worker)\b", r"\bmining\b",
]

# Obvious metropolitan areas where regional eligibility is NOT expected.
_METRO_PATTERNS = [
    r"\bsydney\b", r"\bmelbourne\b", r"\bbrisbane\b", r"\bperth\b", r"\badelaide\b",
    r"\bcanberra\b", r"\bgold coast\b", r"\bnewcastle\b", r"\bwollongong\b",
]

_SAP_PATTERNS = [r"\bsap\b", r"\babap\b", r"\bs/?4\s*hana\b", r"\bfiori\b", r"\bbtp\b"]
_OFFICE_PATTERNS = [
    r"\badmin", r"\breceptionist\b", r"\bdata\s+entry\b", r"\boffice\s+(support|assistant|administrator|manager)\b",
    r"\bcustomer\s+service\b", r"\bclerk\b", r"\bpersonal\s+assistant\b",
]
_HOSP_PATTERNS = [
    r"\bbarista\b", r"\bcafe\b", r"\bcaf[eé]\b", r"\bhospitality\b", r"\bbartender\b",
    r"\bfood\s+and\s+beverage\b", r"\bwaiter\b", r"\bwaitress\b", r"\bkitchen\s+hand\b",
]


def _any(patterns, text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def classify_job(title: str, description: str, location: str, role_track: str = "operational") -> Tuple[str, str]:
    """Returns (category, visa_fit)."""
    title = title or ""
    blob = f"{title} {description or ''}"
    loc = location or ""

    # SAP track decides on the title first to avoid description noise.
    if role_track == "sap" or _any(_SAP_PATTERNS, title):
        return CATEGORY_SAP, VISA_OPEN

    # 88-day signals are matched on the TITLE only: descriptions of warehouse/retail jobs
    # frequently mention words such as "pack" or "grain" in unrelated contexts.
    if _any(_88D_PATTERNS, title):
        fit = VISA_88D_VERIFY if _any(_METRO_PATTERNS, loc) or not loc.strip() else VISA_88D_LIKELY
        return CATEGORY_88D, fit

    if _any(_HOSP_PATTERNS, title):
        return CATEGORY_HOSP, VISA_OPEN
    if _any(_OFFICE_PATTERNS, title):
        return CATEGORY_OFFICE, VISA_OPEN
    return CATEGORY_OTHER, VISA_OPEN


def priority_from_score(score) -> str:
    """Maps the 0-5 match score to a human priority label."""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "Standard"
    if s >= 4.1:
        return "🔥 Hot"
    if s >= 3.9:
        return "⭐ Good"
    return "Standard"


VISA_FIT_LABELS = {
    VISA_88D_LIKELY: "🌾 88d likely (confirm postcode)",
    VISA_88D_VERIFY: "⚠️ 88d? verify postcode",
    VISA_OPEN: "✅ Open market",
}
