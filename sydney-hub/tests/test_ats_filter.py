from hub.discovery.ats_bridge import AtsJob
from hub.discovery.ats_filter import classify_role_bucket, filter_ats_jobs


def test_unlocated_and_generic_sydney_ats_jobs_are_never_kept_without_detail_evidence():
    # Even when an employer has only 1 entry in OSM, empty "" or "Sydney, NSW"
    # without explicit detail evidence MUST be dropped (no OSM single-venue inference).
    jobs = [
        AtsJob("Barista", "https://careers.pageuppeople.com/1/cw/en/job/10/barista", "Local Pub", "", None, None),
        AtsJob("Cleaner", "https://boards.greenhouse.io/x/jobs/2", "Local Pub", "Sydney, NSW", None, None),
    ]
    fetched = []

    def fake_detail(url: str) -> str | None:
        fetched.append(url)
        return "<p>Work across Greater Sydney</p>"

    res = filter_ats_jobs(
        jobs,
        expected_suburb="Rockdale",
        allowlisted_host="careers.pageuppeople.com",
        fetch_detail=fake_detail,
    )
    assert res.kept == []
    assert res.dropped_ambiguous_location == 2
    assert fetched == ["https://careers.pageuppeople.com/1/cw/en/job/10/barista"]


def test_ats_detail_evidence_unlocks_unlocated_pageup_job_only_when_pilot_suburb_named():
    jobs = [
        AtsJob("Bartender", "https://careers.pageuppeople.com/1/cw/en/job/11/bartender", "Pub Group", "", None, None),
    ]
    res = filter_ats_jobs(
        jobs,
        expected_suburb="Rockdale",
        allowlisted_host="careers.pageuppeople.com",
        fetch_detail=lambda _u: "<h1>Bartender</h1><p>Location: Rockdale NSW 2216</p><a href='/apply'>Apply</a>",
    )
    assert len(res.kept) == 1
    assert res.kept[0].matched_suburb == "Rockdale"
    assert res.kept[0].role_bucket == "bartender"
    assert res.kept[0].match_basis == "ats_detail_location"
    assert "Rockdale" in res.kept[0].location_evidence


def test_outside_suburb_and_corporate_roles_are_dropped():
    jobs = [
        AtsJob("Barista", "https://x.example/1", "Cafe Chain", "Parramatta, NSW", "Rockdale Group also operates in Mascot.", None),
        AtsJob("Senior Accountant", "https://x.example/2", "Airport Hotel", "Mascot NSW 2020", None, None),
        AtsJob("Room Attendant / Housekeeping", "https://x.example/3", "Airport Hotel", "Mascot NSW 2020", None, None),
        AtsJob("Kitchen Hand", "https://x.example/4", "Kogarah Bistro", "Kogarah 2217", None, None),
    ]
    res = filter_ats_jobs(jobs, expected_suburb="Mascot", allowlisted_host="x.example")
    assert [(k.job.title, k.matched_suburb, k.role_bucket) for k in res.kept] == [
        ("Room Attendant / Housekeeping", "Mascot", "housekeeping"),
        ("Kitchen Hand", "Kogarah", "kitchen"),
    ]
    assert res.dropped_outside_suburbs == 1
    assert res.dropped_non_target_role == 1


def test_detail_request_budget_is_strictly_capped():
    jobs = [
        AtsJob(f"Cleaner {i}", f"https://x.example/job/{i}", "Cleaning Co", "", None, None)
        for i in range(15)
    ]
    calls = 0

    def fake_detail(_u: str) -> str:
        nonlocal calls
        calls += 1
        return "<p>Location: Hurstville NSW 2220</p>"

    res = filter_ats_jobs(
        jobs,
        expected_suburb="Hurstville",
        allowlisted_host="x.example",
        fetch_detail=fake_detail,
        max_detail_requests=10,
    )
    assert calls == 10
    assert res.detail_requests == 10
    assert len(res.kept) == 10
    assert res.dropped_ambiguous_location == 5


def test_year_2020_in_description_or_url_and_office_perks_are_rejected():
    jobs = [
        AtsJob(
            "Barista",
            "https://x.example/job/2020/barista",
            "Sydney Cafe Co",
            "Sydney, NSW",
            "Founded in 2020, we offer specialty coffee across Sydney.",
            None,
        ),
        AtsJob(
            "Sales Representative",
            "https://x.example/job/99",
            "Hotel Group",
            "Mascot NSW 2020",
            "Great culture, free breakfast and coffee from our barista machine in a clean environment.",
            None,
        ),
        AtsJob(
            "Casual Team Member",
            "https://x.example/job/100",
            "Mascot Bistro",
            "Mascot NSW 2020",
            "Duties include serving drinks as bartender and helping at the bistro.",
            None,
        ),
    ]
    res = filter_ats_jobs(jobs, expected_suburb="Rockdale", allowlisted_host="x.example")
    assert [k.job.title for k in res.kept] == ["Casual Team Member"]
    assert res.dropped_ambiguous_location == 1
    assert res.dropped_non_target_role == 1
    assert classify_role_bucket("Sales Representative", "Free barista coffee daily") is None
