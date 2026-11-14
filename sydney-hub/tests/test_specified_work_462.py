from __future__ import annotations

from hub.domain.specified_work_462 import (
    RULES_VERSION,
    classify_specified_work_462,
)


def test_sydney_metro_hospitality_is_ineligible():
    opp = {
        "id": "syd-1",
        "verification_state": "verified_job",
        "title": "Barista",
        "location": "Rockdale, NSW 2216",
        "suburb": "Rockdale",
        "published_at": "2026-11-12T00:00:00+00:00",
    }
    res = classify_specified_work_462(opp)
    assert res.rules_version == RULES_VERSION
    assert res.status == "ineligible"
    assert res.region_eligible is False
    assert res.postcode == "2216"
    assert any("sydney" in r.lower() or "2216" in r.lower() for r in res.reasons)


def test_northern_australia_verified_hospitality_is_eligible_with_disclaimer():
    opp = {
        "id": "nt-1",
        "verification_state": "verified_job",
        "title": "Bartender / All-Rounder",
        "location": "Darwin, NT 0800",
        "suburb": "Darwin",
        "published_at": "2026-11-12T00:00:00+00:00",
        "pay_hourly_min": 31.0,
    }
    res = classify_specified_work_462(opp)
    assert res.status == "eligible"
    assert res.matched_industry == "tourism_hospitality"
    assert res.postcode == "0800"
    assert res.region_eligible is True
    assert "never guarantees" in res.disclaimer.lower() or "nunca garante" in res.disclaimer.lower()


def test_regional_non_remote_hospitality_is_ineligible_for_462():
    # Tourism & hospitality under 462 requires Northern or Remote/Very Remote Australia,
    # NOT standard regional NSW like Coffs Harbour (2450).
    opp = {
        "id": "coffs-hosp",
        "verification_state": "verified_job",
        "title": "Barista",
        "location": "Coffs Harbour, NSW 2450",
        "published_at": "2026-11-12T00:00:00+00:00",
    }
    res = classify_specified_work_462(opp)
    assert res.status == "ineligible"
    assert res.matched_industry == "tourism_hospitality"


def test_regional_cultivation_verified_is_eligible_but_unverified_needs_review():
    verified_farm = {
        "id": "farm-1",
        "verification_state": "verified_job",
        "title": "Fruit Picker / Harvest Hand",
        "location": "Coffs Harbour, NSW 2450",
        "published_at": "2026-11-12T00:00:00+00:00",
    }
    res_ok = classify_specified_work_462(verified_farm)
    assert res_ok.status == "eligible"
    assert res_ok.matched_industry == "plant_animal_cultivation"

    unverified_farm = dict(verified_farm, id="farm-2", verification_state="unverified_job_post")
    res_rev = classify_specified_work_462(unverified_farm)
    assert res_rev.status == "needs_review"
    assert any("unverified" in m.lower() or "verif" in m.lower() for m in res_rev.missing_facts)


def test_missing_postcode_or_missing_industry_returns_needs_review():
    missing_postcode = {
        "id": "unk-1",
        "verification_state": "verified_job",
        "title": "Farm Hand",
        "location": "Regional NSW",
        "published_at": "2026-11-12T00:00:00+00:00",
    }
    res = classify_specified_work_462(missing_postcode)
    assert res.status == "needs_review"
    assert any("postcode" in m.lower() for m in res.missing_facts)

    missing_title = {
        "id": "unk-2",
        "verification_state": "employer_lead",
        "title": None,
        "location": "Cairns, QLD 4870",
    }
    res2 = classify_specified_work_462(missing_title)
    assert res2.status == "needs_review"
    assert res2.missing_facts
