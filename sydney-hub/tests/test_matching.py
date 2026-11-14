from __future__ import annotations

from datetime import datetime, timedelta, timezone

from hub.domain.matching import (
    CandidateProfile,
    evaluate_opportunity_match,
    resolve_role_alias,
    resolve_unresolved_with_llm,
)


def _now_iso(days_ago: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_role_alias_normalization():
    assert resolve_role_alias("Specialty Coffee Maker / Espresso") == "barista"
    assert resolve_role_alias("Dishhand & Kitchen Prep") == "kitchen"
    assert resolve_role_alias("Pick and Pack Operative") == "warehouse"
    assert resolve_role_alias("Room Attendant") == "housekeeping"
    assert resolve_role_alias("Senior Legal Counsel") is None


def test_verified_matching_returns_explainable_reasons_and_unknowns():
    opp = {
        "id": "job-1",
        "verification_state": "verified_job",
        "title": "Coffee Maker",
        "description": "Casual role, no experience needed, training provided. WHV 462 welcome. RSA required.",
        "employment_type": "Casual",
        "requirements": '["RSA"]',
        "pay_hourly_min": 31.5,
        "pay_display": "$31.50/h",
        "visa_status": "whv_mentioned",
        "published_at": _now_iso(2),
        "last_verified_at": _now_iso(0),
        "commute_windows": {"tuesday_0900": 24},
    }
    profile = CandidateProfile(
        target_roles=("barista",),
        tickets_held=("RSA",),
        english_level="intermediate",
        min_hourly_pay=28.0,
        max_commute_minutes=45,
    )
    res = evaluate_opportunity_match(opp, profile)
    assert res.tier == "strong"
    assert res.score is not None and res.score >= 75
    assert res.matched_role_bucket == "barista"
    reasons_text = " | ".join(res.reasons)
    assert "barista" in reasons_text.lower()
    assert "whv" in reasons_text.lower() or "462" in reasons_text.lower()
    assert "rsa" in reasons_text.lower()
    assert "24 min" in reasons_text
    assert "$31.50/h" in reasons_text
    assert not res.suspicious_signals
    # English level was not stated in the ad, so it must stay explicit in unknowns.
    assert any("english" in u.lower() for u in res.unknowns)


def test_missing_fields_stay_explicit_unknowns_and_leads_have_insufficient_data():
    lead = {
        "id": "lead-1",
        "verification_state": "employer_lead",
        "title": None,
        "company": "Rockdale Cafe",
        "description": None,
        "pay_hourly_min": None,
        "employment_type": None,
        "requirements": None,
        "visa_status": "unknown",
        "published_at": None,
        "last_verified_at": None,
        "commute_windows": {},
    }
    res = evaluate_opportunity_match(lead, CandidateProfile())
    assert res.tier == "insufficient_data"
    assert res.score is None
    unknowns_text = " | ".join(res.unknowns).lower()
    assert "pay" in unknowns_text
    assert "whv" in unknowns_text
    assert "transit" in unknowns_text
    assert "employment type" in unknowns_text


def test_flags_citizen_only_mismatch_and_suspicious_conditions():
    opp = {
        "id": "post-bad",
        "verification_state": "unverified_job_post",
        "title": "Cleaner",
        "description": "Australian citizens or PR only. Cash in hand $19/hr, unpaid trial 2 days.",
        "pay_hourly_min": 19.0,
        "pay_display": "$19/hr",
        "employment_type": None,
        "requirements": None,
        "visa_status": "unknown",
        "published_at": _now_iso(1),
        "last_verified_at": None,
        "commute_windows": {},
    }
    res = evaluate_opportunity_match(opp, CandidateProfile())
    assert res.tier == "weak"
    assert res.score is not None and res.score < 40
    susp_text = " | ".join(res.suspicious_signals).lower()
    assert "cash" in susp_text
    assert "minimum wage" in susp_text or "19.00" in susp_text
    assert "unpaid trial" in susp_text
    assert any("citizen" in r.lower() or "pr only" in r.lower() or "visa" in r.lower() for r in res.reasons + res.suspicious_signals)


def test_unresolved_llm_hook_accepts_grounded_facts_and_rejects_fabrications():
    opp = {
        "id": "job-2",
        "verification_state": "verified_job",
        "title": "All-Rounder",
        "description": "Friendly busy venue in Kogarah. Working holiday makers encouraged to apply. No prior cafe background required.",
        "employment_type": None,
        "requirements": None,
        "visa_status": "unknown",
        "pay_hourly_min": None,
        "commute_windows": {},
    }
    initial = evaluate_opportunity_match(opp, CandidateProfile())
    assert "employment_type" in initial.unresolved_fields
    assert "entry_level" in initial.unresolved_fields

    def fake_llm(_opp, _fields):
        return {
            "employment_type": {
                "value": "Full-time",
                # Fabricated quote not present in description!
                "evidence_quote": "Full-time permanent 38h contract",
            },
            "entry_level": {
                "value": True,
                # Grounded quote present verbatim in description!
                "evidence_quote": "No prior cafe background required",
            },
        }

    resolved = resolve_unresolved_with_llm(opp, initial, fake_llm)
    # Grounded entry_level is accepted; fabricated Full-time is rejected!
    assert any("no prior cafe background required" in r.lower() for r in resolved.reasons)
    assert not any("full-time" in r.lower() for r in resolved.reasons)
    assert any("employment type" in u.lower() for u in resolved.unknowns)
