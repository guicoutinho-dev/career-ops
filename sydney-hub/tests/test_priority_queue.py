from hub.discovery.models import EmployerCandidate
from hub.discovery.priority import build_priority_queue, classify_check_outcome


def test_classify_outcome_never_treats_checked_at_with_error_excerpt_as_reachable():
    assert classify_check_outcome(1794630000.0, "Website check outcome: blocked") == "blocked"
    assert classify_check_outcome(1794630000.0, "Website check outcome: timeout") == "timeout"
    assert classify_check_outcome(1794630000.0, "Website check outcome: error") == "error"
    assert classify_check_outcome(1794630000.0, None, check_outcome="blocked") == "blocked"
    assert classify_check_outcome(1794630000.0, None, check_outcome="ok") == "reachable_ok"
    assert classify_check_outcome(1794630000.0, None) == "reachable_ok"
    assert classify_check_outcome(None, "Website check outcome: ok_no_title") == "reachable_no_title"
    assert classify_check_outcome(None, None, check_outcome="ok_no_title") == "reachable_no_title"
    assert classify_check_outcome(None, None) == "uninspected"


def test_priority_queue_skips_retail_social_and_failed_urls_and_balances_suburbs():
    db_states = [
        {
            "source_url": "https://ok-cafe.example/",
            "company": "OK Cafe",
            "suburb": "Rockdale",
            "category": "cafe",
            "lat": -33.95,
            "lng": 151.14,
            "checked_at": 100.0,
            "excerpt": None,
            "check_outcome": "ok",
        },
        {
            "source_url": "https://blocked-pub.example/",
            "company": "Blocked Pub",
            "suburb": "Mascot",
            "category": "bar",
            "lat": -33.92,
            "lng": 151.19,
            "checked_at": 100.0,
            "excerpt": "Website check outcome: blocked",
            "check_outcome": "blocked",
        },
        {
            "source_url": "https://retail-shop.example/",
            "company": "Optician",
            "suburb": "Hurstville",
            "category": "retail store",
            "lat": -33.96,
            "lng": 151.10,
            "checked_at": 100.0,
            "excerpt": None,
            "check_outcome": "ok",
        },
        {
            "source_url": "https://instagram.com/kogarahcafe",
            "company": "IG Cafe",
            "suburb": "Kogarah",
            "category": "cafe",
            "lat": -33.96,
            "lng": 151.13,
            "checked_at": 100.0,
            "excerpt": None,
            "check_outcome": "ok",
        },
    ]
    pbf = [
        EmployerCandidate("https://new-clean.example/", "OpenStreetMap discovery", "Kogarah", "cleaning service", business_name="Sparkle Clean"),
        EmployerCandidate("https://new-bakery.example/", "OpenStreetMap discovery", "Hurstville", "cafe", business_name="Hot Bake"),
        EmployerCandidate("https://new-shop.example/", "OpenStreetMap discovery", "Mascot", "retail store", business_name="Shoe Store"),
    ]
    res = build_priority_queue(pbf, db_states, max_employers=60, priority="hospitality")
    urls = [c.website_url for c in res.selected]
    assert urls == ["https://ok-cafe.example/", "https://new-clean.example/", "https://new-bakery.example/"]
    assert res.counts["retail_preserved_skipped"] >= 2
    assert res.counts["social_only_skipped"] == 1
    assert res.counts["failed_history_skipped"] == 1
    assert res.counts["tier1_reachable"] == 1
    assert res.counts["tier2_fresh_osm"] == 2
