"""Evidence-backed, time-specific public transit via TfNSW Trip Planner."""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx


_API = "https://api.transport.nsw.gov.au/v1/tp"
_TRIP = f"{_API}/trip"
_SYDNEY = ZoneInfo("Australia/Sydney")
_PUBLIC_TRANSPORT = {1, 4, 5, 7, 9, 11}
_WALK = {99, 100}
_request_lock = threading.Lock()
_last_request = 0.0


@dataclass(frozen=True)
class CommuteResult:
    minutes: int | None
    source: str
    checked_at: float
    outcome: str


def reference_departures(start: date) -> list[tuple[str, datetime]]:
    return [
        (name, datetime.combine(start + timedelta(days=(day - start.weekday()) % 7),
                                datetime.min.time(), _SYDNEY).replace(hour=hour))
        for name, day, hour in (("tuesday_0900", 1, 9), ("friday_1800", 4, 18))
    ]


def _paced_get(client: httpx.Client, path: str, *, api_key: str, params: dict) -> httpx.Response:
    global _last_request
    with _request_lock:
        wait = 0.2 - (time.monotonic() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.monotonic()
    return client.get(f"{_API}/{path}", params=params, headers={"Authorization": f"apikey {api_key}"},
                      timeout=10.0, follow_redirects=False)


def resolve_origin_stop(client: httpx.Client, *, api_key: str | None) -> str | None:
    if not api_key:
        return None
    try:
        response = _paced_get(client, "stop_finder", api_key=api_key, params={
            "outputFormat": "rapidJSON", "coordOutputFormat": "EPSG:4326",
            "type_sf": "any", "name_sf": "Rockdale Station", "anyMaxSizeHitList": 10,
        })
        if response.status_code != 200:
            return None
        locations = response.json().get("locations", [])
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    if not isinstance(locations, list):
        return None
    for location in locations:
        if not isinstance(location, dict):
            continue
        name, stop_id = location.get("name"), location.get("id")
        if isinstance(name, str) and name.lower().startswith("rockdale station") and str(stop_id).isdigit():
            return str(stop_id)
    return None


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def _journey_minutes(journey: object, departure_at: datetime) -> int | None:
    if not isinstance(journey, dict) or not isinstance(journey.get("legs"), list) or not journey["legs"]:
        return None
    legs = journey["legs"]
    classes = []
    for leg in legs:
        if not isinstance(leg, dict):
            return None
        transport = leg.get("transportation") or {}
        product = transport.get("product") or {} if isinstance(transport, dict) else {}
        route_class = product.get("class") if isinstance(product, dict) else None
        if route_class not in _PUBLIC_TRANSPORT | _WALK:
            return None
        classes.append(route_class)
    if not any(route_class in _PUBLIC_TRANSPORT for route_class in classes):
        return None
    first_origin = legs[0].get("origin") or {}
    last_destination = legs[-1].get("destination") or {}
    first = _parse_time(first_origin.get("departureTimePlanned")) if isinstance(first_origin, dict) else None
    final = _parse_time(last_destination.get("arrivalTimePlanned")) if isinstance(last_destination, dict) else None
    if not first or not final or first < departure_at or final < first:
        return None
    return math.ceil((final - departure_at).total_seconds() / 60)


def measure_commute(
    origin_stop: str, destination: tuple[float, float] | None, departure_at: datetime,
    *, client: httpx.Client, api_key: str | None,
) -> CommuteResult:
    checked_at = time.time()
    if (not api_key or not origin_stop.isdigit() or destination is None or len(destination) != 2
            or departure_at.tzinfo is None):
        return CommuteResult(None, _TRIP, checked_at, "missing_input")
    lat, lng = destination
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lat, lng)) or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return CommuteResult(None, _TRIP, checked_at, "missing_input")
    local = departure_at.astimezone(_SYDNEY)
    params = {
        "outputFormat": "rapidJSON", "coordOutputFormat": "EPSG:4326",
        "depArrMacro": "dep", "itdDate": local.strftime("%Y%m%d"), "itdTime": local.strftime("%H%M"),
        "type_origin": "stop", "name_origin": origin_stop,
        "type_destination": "coord", "name_destination": f"{lng:.6f}:{lat:.6f}:EPSG:4326",
        "TfNSWTR": "true",
    }
    try:
        response = _paced_get(client, "trip", api_key=api_key, params=params)
        if response.status_code != 200:
            return CommuteResult(None, _TRIP, checked_at, f"http_{response.status_code}")
        payload = response.json()
        journeys = payload.get("journeys") if isinstance(payload, dict) else None
    except (httpx.HTTPError, ValueError):
        return CommuteResult(None, _TRIP, checked_at, "network_error")
    if not isinstance(journeys, list):
        return CommuteResult(None, _TRIP, checked_at, "invalid_response")
    minutes = [value for journey in journeys if (value := _journey_minutes(journey, departure_at)) is not None]
    return CommuteResult(min(minutes), _TRIP, checked_at, "ok") if minutes else CommuteResult(None, _TRIP, checked_at, "no_route")
