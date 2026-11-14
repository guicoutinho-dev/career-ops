import gzip

import httpx
import pytest

from hub.discovery.workable import WORKABLE_FEED_URL, fetch_workable, iter_workable_listings


def feed(*jobs: str) -> bytes:
    return ("<?xml version='1.0' encoding='utf-8'?><source><publisher>Workable</publisher>"
            + "".join(jobs) + "</source>").encode()


AU_JOB = """<job><title><![CDATA[Barista & All-Rounder]]></title>
<date><![CDATA[Sun, 08 Nov 2026 11:02:25 UTC]]></date>
<referencenumber><![CDATA[ABC123]]></referencenumber>
<url><![CDATA[https://apply.workable.com/cafe/j/ABC123/]]></url>
<company><![CDATA[Example Cafe]]></company><city><![CDATA[Rockdale]]></city>
<state><![CDATA[NSW]]></state><country><![CDATA[AU]]></country>
<postalcode><![CDATA[2216]]></postalcode><jobtype><![CDATA[]]></jobtype>
<description><![CDATA[<p>Morning shifts</p>]]></description></job>"""

US_JOB = """<job><title>Barista</title><url>https://apply.workable.com/us/j/US1/</url>
<company>US Cafe</company><city>Portland</city><country>US</country></job>"""


def test_streamed_au_job_preserves_source_facts_and_skips_other_countries():
    raw = feed(AU_JOB, US_JOB)
    chunks = (raw[i:i + 7] for i in range(0, len(raw), 7))
    listings = list(iter_workable_listings(chunks))
    assert len(listings) == 1
    item = listings[0]
    assert item.source_id == "workable"
    assert item.external_id == "ABC123"
    assert item.source_url == "https://apply.workable.com/cafe/j/ABC123/"
    assert item.title == "Barista & All-Rounder"
    assert item.company == "Example Cafe"
    assert item.location == "Rockdale NSW 2216"
    assert item.published_at.isoformat() == "2026-11-08T11:02:25+00:00"
    assert item.employment_type is None
    assert item.description == "<p>Morning shifts</p>"
    assert item.lat is None and item.lng is None


def test_missing_date_and_location_stay_unknown():
    raw = feed("<job><title>Waitstaff</title><url>https://apply.workable.com/cafe/j/2</url>"
               "<company>Cafe</company><country>AU</country></job>")
    item, = iter_workable_listings([raw])
    assert item.published_at is None
    assert item.location is None


def test_date_without_timezone_is_not_assigned_machine_local_time():
    raw = feed("<job><title>Waitstaff</title><date>08 Nov 2026 11:00:00</date>"
               "<url>https://apply.workable.com/cafe/j/2</url><company>Cafe</company><country>AU</country></job>")
    item, = iter_workable_listings([raw])
    assert item.published_at is None


@pytest.mark.parametrize("raw", [
    feed(AU_JOB)[:-9],
    b"<!DOCTYPE source [<!ENTITY x 'unsafe'>]><source><job>&x;</job></source>",
])
def test_malformed_or_entity_feed_fails_closed(raw: bytes):
    with pytest.raises(ValueError):
        list(iter_workable_listings([raw]))


def test_byte_and_record_caps_fail_closed():
    raw = feed(AU_JOB, AU_JOB)
    with pytest.raises(ValueError, match="byte"):
        list(iter_workable_listings([raw], max_bytes=len(raw) - 1))
    with pytest.raises(ValueError, match="record"):
        list(iter_workable_listings([raw], max_jobs=1))


def test_fetch_uses_only_fixed_feed_endpoint_and_rejects_redirect():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, content=feed(AU_JOB))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        jobs = fetch_workable(client)
    assert requests == [WORKABLE_FEED_URL]
    assert len(jobs) == 1

    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(302, headers={"location": "https://other.example/feed"}))) as client:
        with pytest.raises(ValueError, match="redirect"):
            fetch_workable(client)


def test_fetch_streams_gzip_wire_beyond_100k_non_au_records():
    us_stub = "<job><country>US</country></job>"
    payload = gzip.compress(feed(*(us_stub for _ in range(100_005)), AU_JOB), compresslevel=1)
    with httpx.Client(transport=httpx.MockTransport(
        lambda _r: httpx.Response(
            200, stream=httpx.ByteStream(payload),
            headers={"content-encoding": "gzip", "transfer-encoding": "chunked"},
        )
    )) as client:
        jobs = fetch_workable(client)
    assert len(jobs) == 1
    assert jobs[0].external_id == "ABC123"


def test_fetch_rejects_wire_cap_gzip_bomb_oversized_node_and_kept_cap():
    blob = AU_JOB.replace("Morning shifts", "A" * 16_384)
    compressed = gzip.compress(feed(blob, blob))
    with httpx.Client(transport=httpx.MockTransport(
        lambda _r: httpx.Response(
            200, stream=httpx.ByteStream(compressed),
            headers={"content-encoding": "gzip", "transfer-encoding": "chunked"},
        )
    )) as client:
        with pytest.raises(ValueError, match="wire"):
            fetch_workable(client, max_wire_bytes=len(compressed) - 1)
        with pytest.raises(ValueError, match="ratio"):
            fetch_workable(client, max_inflation_ratio=2, min_ratio_check_bytes=1024)
        with pytest.raises(ValueError, match="kept"):
            fetch_workable(client, max_kept=1)

    with pytest.raises(ValueError, match="element"):
        list(iter_workable_listings([feed(blob)], max_node_bytes=4096))
