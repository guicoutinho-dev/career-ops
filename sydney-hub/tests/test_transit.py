from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx

from hub.discovery.transit import measure_commute, reference_departures, resolve_origin_stop


SYDNEY = ZoneInfo("Australia/Sydney")


def journey(depart: str, arrive: str, classes=(1, 99)):
    legs = []
    for i, product_class in enumerate(classes):
        legs.append({
            "origin": {"departureTimePlanned": depart},
            "destination": {"arrivalTimePlanned": arrive},
            "transportation": {"product": {"class": product_class}},
        })
    return {"legs": legs}


def test_reference_windows_are_sydney_local_with_dst():
    summer = reference_departures(date(2026, 11, 14))
    assert [(name, when.strftime("%Y%m%d %H%M %z")) for name, when in summer] == [
        ("tuesday_0900", "20261117 0900 +1100"),
        ("friday_1800", "20261120 1800 +1100"),
    ]
    winter = reference_departures(date(2026, 6, 1))
    assert [when.strftime("%z") for _, when in winter] == ["+1000", "+1000"]


def test_trip_request_uses_official_stop_and_coordinates_with_header_only_key():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"journeys": [journey(
            "2026-11-17T09:10:00+11:00", "2026-11-17T09:40:00+11:00") ]})

    departure = datetime(2026, 11, 17, 9, 0, tzinfo=SYDNEY)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = measure_commute("221620", (-33.923, 151.187), departure, client=client, api_key="secret")
    assert result.minutes == 40  # includes ten minutes before the first service
    assert result.outcome == "ok"
    assert result.source == "https://api.transport.nsw.gov.au/v1/tp/trip"
    assert len(requests) == 1
    request = requests[0]
    assert request.headers["Authorization"] == "apikey secret"
    assert "secret" not in str(request.url) and "secret" not in repr(result)
    assert request.url.params["outputFormat"] == "rapidJSON"
    assert request.url.params["coordOutputFormat"] == "EPSG:4326"
    assert request.url.params["depArrMacro"] == "dep"
    assert request.url.params["itdDate"] == "20261117"
    assert request.url.params["itdTime"] == "0900"
    assert request.url.params["type_origin"] == "stop"
    assert request.url.params["name_origin"] == "221620"
    assert request.url.params["type_destination"] == "coord"
    assert request.url.params["name_destination"] == "151.187000:-33.923000:EPSG:4326"


def test_car_only_earlier_trip_and_missing_route_are_unknown():
    departure = datetime(2026, 11, 20, 18, 0, tzinfo=SYDNEY)
    payload = {"journeys": [
        journey("2026-11-20T18:00:00+11:00", "2026-11-20T18:15:00+11:00", (0,)),
        journey("2026-11-20T17:50:00+11:00", "2026-11-20T18:20:00+11:00"),
    ]}
    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=payload))) as client:
        result = measure_commute("221620", (-33.923, 151.187), departure, client=client, api_key="key")
    assert result.minutes is None and result.outcome == "no_route"


def test_no_key_or_destination_makes_no_request():
    departure = datetime(2026, 11, 17, 9, 0, tzinfo=SYDNEY)
    with httpx.Client(transport=httpx.MockTransport(lambda _r: (_ for _ in ()).throw(AssertionError("network")))) as client:
        assert measure_commute("221620", None, departure, client=client, api_key="key").minutes is None
        assert measure_commute("221620", (-33.923, 151.187), departure, client=client, api_key=None).minutes is None


def test_rockdale_origin_must_match_official_stop_finder():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"locations": [
            {"name": "Rockdale Station, Rockdale", "id": "221620"},
            {"name": "Rockdale Rd", "id": "wrong"},
        ]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert resolve_origin_stop(client, api_key="key") == "221620"
    assert requests[0].url.path == "/v1/tp/stop_finder"
    assert requests[0].url.params["name_sf"] == "Rockdale Station"
    assert requests[0].headers["Authorization"] == "apikey key"
