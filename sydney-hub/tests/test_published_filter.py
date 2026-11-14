from datetime import datetime, timezone

import pytest

from hub.discovery.published import PublishedListing
from hub.discovery.published_filter import classify_published


def listing(title: str, location: str | None, *, lat: float | None = None, lng: float | None = None) -> PublishedListing:
    return PublishedListing(
        source_id="workable", external_id="123", source_url="https://apply.workable.com/example/j/123/",
        title=title, company="Example", location=location, published_at=datetime(2026, 11, 14, tzinfo=timezone.utc),
        employment_type=None, description=None, lat=lat, lng=lng,
    )


@pytest.mark.parametrize(("title", "bucket"), [
    ("Barista", "barista"), ("Bar Staff", "bartender"), ("Kitchen Hand", "kitchen"),
    ("Waitstaff", "front_of_house"), ("Room Attendant", "housekeeping"),
    ("Retail Assistant", "retail"), ("Shop Assistant", "retail"),
    ("Warehouse Picker/Packer", "warehouse"), ("Pick and Pack Team Member", "warehouse"),
])
def test_target_role_synonyms(title: str, bucket: str):
    match = classify_published(listing(title, "Rockdale NSW"))
    assert match.role_bucket == bucket
    assert match.region_eligible is True
    assert match.location_tier == "suburb_only"


@pytest.mark.parametrize("title", [
    "Retail Marketing Analyst", "Warehouse Operations Manager", "Senior Accountant - Barista Division",
    "Deckhand | Captain Cook Cruises", "Mechanic All Rounder | Captain Cook Cruises",
    "Master less than 45m - Casual | Captain Cook Cruises",
    "Medical Receptionist", "Part-Time/Full-Time Research Panel. Work from Home",
    "Sales Consultant (Food & Beverage) - Sydney",
])
def test_corporate_titles_do_not_match(title: str):
    assert classify_published(listing(title, "Sydney NSW")).role_bucket is None


def test_generic_sydney_is_regional_but_location_is_approximate():
    match = classify_published(listing("Barista", "Sydney NSW"))
    assert match.region_eligible is True
    assert match.location_tier == "unknown"


def test_explicit_outside_nsw_and_missing_location_are_distinct():
    outside = classify_published(listing("Barista", "Melbourne VIC"))
    missing = classify_published(listing("Barista", None))
    assert outside.region_eligible is False
    assert missing.region_eligible is None
    assert outside.location_tier == missing.location_tier == "unknown"


def test_coordinates_make_location_exact_only_with_region_evidence():
    exact = classify_published(listing("Barista", "Mascot NSW", lat=-33.923, lng=151.187))
    ambiguous = classify_published(listing("Barista", None, lat=-33.923, lng=151.187))
    assert exact.location_tier == "exact"
    assert ambiguous.location_tier == "unknown"


def test_unspecified_requirements_and_contract_remain_unknown():
    match = classify_published(listing("Barista", "Kogarah NSW"))
    assert match.employment_type is None
    assert match.rsa_required is None
    assert match.rcg_required is None
    assert match.whv_eligible is None
    assert match.entry_level is None
