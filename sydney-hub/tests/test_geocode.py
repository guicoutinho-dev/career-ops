from datetime import datetime, timezone
import json

import httpx

from hub.discovery.geocode import (
    ExtractedLocationEvidence,
    extract_posting_location_evidence,
    geocode_explicit_street_address,
    is_explicit_nsw_street_address,
)
from hub.discovery.published import PublishedListing
from hub.discovery.published_filter import classify_published


NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


def test_generic_location_and_suburb_only_remain_unknown_while_explicit_street_address_geocodes():
    assert is_explicit_nsw_street_address("Sydney NSW") is False
    assert is_explicit_nsw_street_address("Rockdale NSW") is False
    assert is_explicit_nsw_street_address("Greater Sydney, NSW 2000") is False
    assert is_explicit_nsw_street_address("452 Princes Hwy, Rockdale NSW 2216") is True

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "nominatim.openstreetmap.org" in str(request.url):
            return httpx.Response(200, json=[{"lat": "-33.9524", "lon": "151.1376"}])
        if "api.transport.nsw.gov.au" in str(request.url):
            return httpx.Response(200, json={"locations": [{"coord": [-33.9524, 151.1376], "type": "street"}]})
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert geocode_explicit_street_address("Sydney NSW", client=client, enabled=True) is None
        assert geocode_explicit_street_address("Rockdale NSW", client=client, enabled=True) is None
        assert calls == []

        assert geocode_explicit_street_address("452 Princes Hwy, Rockdale NSW 2216", client=client, enabled=False) is None
        assert calls == []

        coords = geocode_explicit_street_address("452 Princes Hwy, Rockdale NSW 2216", client=client, enabled=True)
        assert coords == (-33.9524, 151.1376)
        assert len(calls) == 1

        tfnsw_coords = geocode_explicit_street_address(
            "452 Princes Hwy, Rockdale NSW 2216",
            client=client,
            tfnsw_api_key="test-key",
            enabled=True,
            min_interval_seconds=0.0,
        )
        assert tfnsw_coords == (-33.9524, 151.1376)

    def outside_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"lat": "-37.8136", "lon": "144.9631"}])

    with httpx.Client(transport=httpx.MockTransport(outside_handler)) as outside_client:
        assert geocode_explicit_street_address(
            "452 Princes Hwy, Rockdale NSW 2216",
            client=outside_client,
            enabled=True,
            min_interval_seconds=0.0,
        ) is None

    sydney_match = classify_published(
        PublishedListing("workable", "W1", "https://apply.workable.com/j/W1", "Barista", "Cafe", "Sydney NSW", NOW, None, None, None, None)
    )
    rockdale_match = classify_published(
        PublishedListing("workable", "W2", "https://apply.workable.com/j/W2", "Barista", "Cafe", "Rockdale NSW", NOW, None, None, None, None)
    )
    assert sydney_match.location_tier == "unknown"
    assert rockdale_match.location_tier == "suburb_only"


def test_extract_posting_location_evidence_parses_jsonld_geo_and_street_address():
    html_with_geo = (
        '<html><head><script type="application/ld+json">'
        + json.dumps({
            "@context": "https://schema.org",
            "@type": "JobPosting",
            "title": "Barista",
            "jobLocation": {
                "@type": "Place",
                "geo": {"@type": "GeoCoordinates", "latitude": -33.951, "longitude": 151.138},
                "address": {
                    "@type": "PostalAddress",
                    "streetAddress": "452 Princes Hwy",
                    "addressLocality": "Rockdale",
                    "addressRegion": "NSW",
                    "postalCode": "2216",
                },
            },
        })
        + "</script></head><body><h1>Barista</h1></body></html>"
    )
    ev = extract_posting_location_evidence(
        html_with_geo, content_type="text/html", evidence_url="https://example.com/jobs/1"
    )
    assert isinstance(ev, ExtractedLocationEvidence)
    assert ev.lat == -33.951 and ev.lng == 151.138
    assert ev.source_basis == "jsonld_geo"
    assert ev.street_address == "452 Princes Hwy, Rockdale NSW 2216"

    html_street_only = (
        '<html><head><script type="application/ld+json">'
        + json.dumps({
            "@type": "JobPosting",
            "jobLocation": {
                "address": {
                    "streetAddress": "452 Princes Hwy",
                    "addressLocality": "Rockdale",
                    "addressRegion": "NSW",
                    "postalCode": "2216",
                }
            },
        })
        + "</script></head></html>"
    )
    ev_street = extract_posting_location_evidence(
        html_street_only, content_type="text/html", evidence_url="https://example.com/jobs/2"
    )
    assert ev_street.lat is None and ev_street.lng is None
    assert ev_street.street_address == "452 Princes Hwy, Rockdale NSW 2216"
    assert ev_street.source_basis == "none"

    workable_generic = json.dumps({"shortcode": "W1", "location": {"city": "Sydney", "region": "NSW"}})
    ev_generic = extract_posting_location_evidence(
        workable_generic, content_type="application/json", evidence_url="https://www.workable.com/api/v2/accounts/cafe/jobs/W1"
    )
    assert ev_generic.lat is None and ev_generic.lng is None and ev_generic.street_address is None
    assert ev_generic.source_basis == "none"

    workable_geo = json.dumps({"shortcode": "W2", "location": {"lat": -33.95, "lng": 151.14}})
    ev_api = extract_posting_location_evidence(
        workable_geo, content_type="application/json", evidence_url="https://www.workable.com/api/v2/accounts/cafe/jobs/W2"
    )
    assert ev_api.lat == -33.95 and ev_api.lng == 151.14 and ev_api.source_basis == "api_geo"
