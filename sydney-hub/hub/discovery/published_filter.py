"""Conservative role and region labels for source-published listings."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from hub.discovery.ats_filter import _CORPORATE_TITLE, classify_role_bucket
from hub.discovery.published import PublishedListing


_RETAIL = re.compile(r"\b(?:retail|shop|store)\s+(?:assistants?|attendants?|team\s+members?)\b", re.I)
_WAREHOUSE = re.compile(r"\b(?:warehouse\s+(?:picker|packer|hands?|operatives?)|pick(?:er|ing)?\s*(?:/|and|&)\s*pack(?:er|ing)?)\b", re.I)
_NON_OPERATIONAL = re.compile(r"\b(?:managers?|directors?|analysts?|coordinators?|supervisors?)\b", re.I)
_EXCLUDED_PUBLISHED_TITLE = re.compile(
    r"\b(?:deckhand|mechanic(?:\s+all[- ]?rounder)?|master\s+less\s+than|"
    r"medical\s+receptionists?|research\s+panel|sales\s+consultants?)\b",
    re.I,
)
_PILOT_SUBURB = re.compile(r"\b(?:Rockdale|Kogarah|Hurstville|Mascot)\b", re.I)
_NSW = re.compile(r"\b(?:NSW|New\s+South\s+Wales)\b", re.I)
_SYDNEY = re.compile(r"\b(?:Sydney|Greater\s+Sydney)\b", re.I)
_OUTSIDE_NSW = re.compile(r"\b(?:VIC|QLD|WA|SA|TAS|ACT|NT|Victoria|Queensland|Tasmania|Melbourne|Brisbane|Perth|Adelaide|Canberra|Hobart|Darwin)\b", re.I)
_GENERIC = re.compile(r"^(?:Greater\s+Sydney|Sydney(?:\s+(?:Metro|CBD))?|NSW|New\s+South\s+Wales|Australia)(?:\s*,?\s*(?:NSW|New\s+South\s+Wales|Australia))?(?:\s+\d{4})?$", re.I)


@dataclass(frozen=True)
class PublishedMatch:
    role_bucket: str | None
    region_eligible: bool | None
    location_tier: Literal["exact", "suburb_only", "unknown"]
    employment_type: str | None
    rsa_required: bool | None = None
    rcg_required: bool | None = None
    whv_eligible: bool | None = None
    entry_level: bool | None = None


def classify_published(listing: PublishedListing) -> PublishedMatch:
    title = listing.title or ""
    if _EXCLUDED_PUBLISHED_TITLE.search(title):
        role = None
    else:
        role = classify_role_bucket(title, listing.description)
    if role is None and not (_CORPORATE_TITLE.search(title) or _NON_OPERATIONAL.search(title) or _EXCLUDED_PUBLISHED_TITLE.search(title)):
        if _RETAIL.search(title):
            role = "retail"
        elif _WAREHOUSE.search(title):
            role = "warehouse"

    location = (listing.location or "").strip()
    if _OUTSIDE_NSW.search(location):
        region: bool | None = False
    elif _PILOT_SUBURB.search(location) or _NSW.search(location) or _SYDNEY.search(location):
        region = True
    else:
        region = None

    if region is True and listing.lat is not None and listing.lng is not None and -90 <= listing.lat <= 90 and -180 <= listing.lng <= 180:
        tier: Literal["exact", "suburb_only", "unknown"] = "exact"
    elif region is True and location and not _GENERIC.fullmatch(location):
        tier = "suburb_only"
    else:
        tier = "unknown"
    return PublishedMatch(role, region, tier, listing.employment_type)
