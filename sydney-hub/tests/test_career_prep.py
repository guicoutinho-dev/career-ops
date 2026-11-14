from __future__ import annotations

from hub.career.prep import load_candidate_facts, prepare_application_packet


def test_load_candidate_facts_handles_missing_files_without_fabrication(tmp_path):
    facts = load_candidate_facts(tmp_path)
    assert facts.source_files == []
    assert facts.name is None
    assert facts.tickets_held == []
    assert facts.experience_highlights == []
    assert len(facts.missing_profile_sources) > 0


def test_load_candidate_facts_reads_profile_and_cv_only(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "profile.yml").write_text(
        "candidate:\n"
        "  name: Gui\n"
        "  location: Rockdale, NSW\n"
        "  visa: Work and Holiday 462\n"
        "  languages:\n"
        "    - Portuguese\n"
        "    - English\n"
        "  tickets:\n"
        "    - RSA NSW\n",
        encoding="utf-8",
    )
    (tmp_path / "cv.md").write_text(
        "# Gui\n\n- Customer service & coffee preparation experience\n- Warehouse packing and stocktake\n",
        encoding="utf-8",
    )
    # Example / harness files must NEVER be treated as candidate ground truth
    (tmp_path / "config" / "profile.example.yml").write_text(
        "candidate:\n  name: Santiago Fake\n  tickets:\n    - White Card\n",
        encoding="utf-8",
    )
    (tmp_path / "USER.md").write_text(
        "- Prefer concise progress updates\n",
        encoding="utf-8",
    )

    facts = load_candidate_facts(tmp_path)
    assert "USER.md" not in facts.source_files
    assert not any("concise progress" in h.lower() for h in facts.experience_highlights)
    assert "config/profile.yml" in facts.source_files
    assert "cv.md" in facts.source_files
    assert facts.name == "Gui"
    assert facts.suburb == "Rockdale, NSW"
    assert facts.visa_status == "Work and Holiday 462"
    assert "RSA NSW" in facts.tickets_held
    assert "White Card" not in facts.tickets_held
    assert any("coffee preparation" in line.lower() for line in facts.experience_highlights)


def test_prepare_application_packet_never_auto_submits_and_flags_missing_and_leads(tmp_path):
    facts = load_candidate_facts(tmp_path)  # empty profile

    unverified_cash_post = {
        "id": "fb_1",
        "title": "Barista needed",
        "company": None,
        "suburb": "Bondi",
        "source": "Facebook group",
        "source_url": "https://facebook.com/groups/1/posts/99",
        "verification_state": "unverified_job_post",
        "lifecycle_state": "active",
        "requirements": "RSA required",
        "pay_raw": "cash in hand $20/h",
        "employment_type": None,
        "evidence": [{"source_url": "https://facebook.com/groups/1/posts/99", "source_type": "social_post", "supports_job": False}],
    }
    packet = prepare_application_packet(unverified_cash_post, facts)
    assert packet["auto_submit"] is False
    assert packet["submission_mode"] == "manual_review_only"
    assert packet["suspicious_warnings"]
    assert any("RSA" in item for item in packet["missing_facts_to_confirm"])
    assert "[Your Name]" in packet["outreach_draft_en"]
    assert "RSA" not in packet["outreach_draft_en"]  # Never claim a ticket the candidate does not hold

    lead = {
        "id": "osm_1",
        "title": "Hospitality / Cafe (OSM discovery)",
        "company": "Bayview Cafe",
        "suburb": "Brighton-Le-Sands",
        "source": "OpenStreetMap discovery",
        "source_url": "https://bayview.example.com.au",
        "verification_state": "employer_lead",
        "lifecycle_state": "active",
        "evidence": [{"source_url": "https://bayview.example.com.au", "source_type": "employer_website", "supports_job": False}],
    }
    lead_packet = prepare_application_packet(lead, facts)
    assert lead_packet["auto_submit"] is False
    assert "proactive" in lead_packet["channel_guidance"].lower() or "lead" in lead_packet["channel_guidance"].lower()
    assert "published" not in lead_packet["outreach_draft_en"].lower()
