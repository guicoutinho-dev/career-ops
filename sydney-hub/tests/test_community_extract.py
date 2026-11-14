from __future__ import annotations

import json
import time

from hub.domain.community_extract import extract_community_post
from hub.domain.models import AiStatus, Classification, Post, ScrapedPost
from hub.storage.catalog import CatalogRepository
from hub.storage.db import Database
from hub.storage.posts import PostRepository


def test_extracts_explicit_role_suburb_pay_contact_terms_and_excerpts():
    body = (
        "Hiring casual Barista in Rockdale, $32/hr + super. "
        "RSA required. Call 0412 345 678. WHV 462 welcome!"
    )
    res = extract_community_post(body, created_ts=1763000000.0)
    assert res.title == "Barista"
    assert res.role_bucket == "barista"
    assert res.suburb == "Rockdale"
    assert res.pay_hourly_min == 32.0
    assert res.pay_display == "$32/hr"
    assert res.employment_type == "Casual"
    assert res.requirements == ["RSA"]
    assert res.whv_eligible is True
    assert res.contact is not None and "0412 345 678" in res.contact
    assert res.published_at is not None and res.published_at.startswith("2025-") or res.published_at.startswith("202")
    for key in ("title", "suburb", "pay", "employment_type", "requirements", "contact", "whv_eligible"):
        assert key in res.excerpts
        assert res.excerpts[key] in body


def test_never_infers_certification_from_role_name_or_defaults_ticket():
    body = "Need a Bartender for Friday night in Mascot, $35/h"
    res = extract_community_post(body, created_ts=1763000000.0)
    assert res.title == "Bartender"
    assert res.role_bucket == "bartender"
    assert res.suburb == "Mascot"
    assert res.pay_hourly_min == 35.0
    assert res.requirements is None
    assert res.employment_type is None
    assert res.whv_eligible is None


def test_ambiguous_post_stays_unverified_with_null_fields():
    body = "Preciso de alguem pra ajudar fim de semana, chamar inbox"
    res = extract_community_post(body, created_ts=1763000000.0)
    assert res.title is None
    assert res.suburb is None
    assert res.pay_hourly_min is None
    assert res.pay_display is None
    assert res.employment_type is None
    assert res.requirements is None
    assert res.whv_eligible is None
    assert res.contact_excerpt is not None


def test_detects_suspicious_community_signals():
    body = "Cleaner needed in Hurstville, cash in hand $18/hr, unpaid trial 3 days, bring passport to hold"
    res = extract_community_post(body, created_ts=1763000000.0)
    signals = " | ".join(res.suspicious_signals).lower()
    assert "cash" in signals or "below" in signals
    assert "unpaid" in signals
    assert "passport" in signals


def test_sync_posts_never_infers_employer_from_author_and_stores_provenance(tmp_path):
    db = Database(tmp_path / "hub.db")
    posts_repo = PostRepository(db)
    catalog = CatalogRepository(db)

    scraped = ScrapedPost(
        post_id="fb_post_101",
        group_url="https://www.facebook.com/groups/testgroup",
        group_name="Brasileiros em Sydney",
        permalink="https://www.facebook.com/groups/testgroup/posts/101",
        author="Maria Silva",
        author_url="https://www.facebook.com/maria.silva",
        body="Hiring kitchen hand in Kogarah, $30/h casual. Send message or call 0499 111 222.",
        created_ts=1763000000.0,
    )
    post = Post(
        scraped=scraped,
        classification=Classification(kind="job", score=65, suburb="Kogarah", pay_hourly=30),
        ai_status=AiStatus.SKIPPED,
        first_seen_at=time.time(),
    )
    posts_repo.add(post)
    catalog.sync_posts()

    items = catalog.list()
    assert len(items) == 1
    opp = items[0]
    assert opp["verification_state"] == "unverified_job_post"
    assert opp["employer_id"] is None
    assert opp["company"] is None
    assert opp["title"] == "Kitchen Hand"
    assert opp["suburb"] == "Kogarah"
    assert opp["pay_hourly_min"] == 30.0
    assert opp["pay_display"] == "$30/h"
    assert opp["employment_type"] == "Casual"
    assert opp["requirements"] is None
    assert opp["visa_status"] == "unknown"
    assert opp["role_bucket"] == "kitchen"
    fb_ev = [e for e in opp["evidence"] if e["source_type"] == "facebook_post"]
    assert len(fb_ev) == 1
    assert fb_ev[0]["supports_job"] is False
    provenance = json.loads(fb_ev[0]["excerpt"])
    assert provenance["excerpts"]["title"] in scraped.body
    assert provenance["excerpts"]["pay"] in scraped.body
    assert provenance["excerpts"]["suburb"] in scraped.body
