"""Exact street-address extraction and non-fabricating free-tier geocoding."""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx


_SYDNEY_LAT_MIN = -34.25
_SYDNEY_LAT_MAX = -33.45
_SYDNEY_LNG_MIN = 150.45
_SYDNEY_LNG_MAX = 151.55

_STREET_NUMBER_RE = re.compile(r"\b\d+[A-Za-z]?(?:-\d+)?\s+[A-Za-z]")
_STREET_TYPE_RE = re.compile(
    r"\b(?:st|street|rd|road|ave|avenue|hwy|highway|pde|parade|dr|drive|ln|lane|"
    r"pl|place|cres|crescent|cl|close|ct|court|blvd|boulevard|tce|terrace|way|mall|sq|square)\b",
    re.I,
)
_NSW_POSTCODE_RE = re.compile(r"\b(?:NSW|New\s+South\s+Wales)\b(?:\s*,?\s*2\d{3})?\b", re.I)
_GENERIC_LOCALITY_RE = re.compile(
    r"^(?:Greater\s+Sydney|Sydney(?:\s+(?:Metro|CBD))?|NSW|New\s+South\s+Wales|Australia)"
    r"(?:\s*,?\s*(?:NSW|New\s+South\s+Wales|Australia))?(?:\s+\d{4})?$",
    re.I,
)
_JSONLD_SCRIPT_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)

_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0


@dataclass(frozen=True)
class ExtractedLocationEvidence:
    lat: float | None
    lng: float | None
    street_address: str | None
    source_basis: Literal["api_geo", "jsonld_geo", "street_geocoded", "none"]
    evidence_url: str | None


def _in_greater_sydney(lat: float, lng: float) -> bool:
    return _SYDNEY_LAT_MIN <= lat <= _SYDNEY_LAT_MAX and _SYDNEY_LNG_MIN <= lng <= _SYDNEY_LNG_MAX


def _coerce_coords(lat_raw: Any, lng_raw: Any) -> tuple[float, float] | None:
    try:
        lat = float(lat_raw)
        lng = float(lng_raw)
    except (TypeError, ValueError):
        return None
    return (lat, lng) if _in_greater_sydney(lat, lng) else None


def is_explicit_nsw_street_address(address: str | None) -> bool:
    """Return True only when `address` has a street number, street type, and NSW locality."""
    text = " ".join((address or "").split()).strip()
    if not text or _GENERIC_LOCALITY_RE.fullmatch(text):
        return False
    return bool(
        _STREET_NUMBER_RE.search(text)
        and _STREET_TYPE_RE.search(text)
        and _NSW_POSTCODE_RE.search(text)
    )


def _format_postal_address(addr: Any) -> str | None:
    if not isinstance(addr, dict):
        return None
    street = " ".join(str(addr.get("streetAddress") or "").split()).strip()
    locality = " ".join(str(addr.get("addressLocality") or "").split()).strip()
    region = " ".join(str(addr.get("addressRegion") or "").split()).strip()
    postal = " ".join(str(addr.get("postalCode") or "").split()).strip()
    if not street or not locality:
        return None
    tail = " ".join(part for part in (region or "NSW", postal) if part)
    formatted = f"{street}, {locality} {tail}".strip()
    return formatted if is_explicit_nsw_street_address(formatted) else None


def _iter_jsonld_objects(node: Any) -> list[dict]:
    found: list[dict] = []
    if isinstance(node, dict):
        found.append(node)
        graph = node.get("@graph")
        if graph is not None:
            found.extend(_iter_jsonld_objects(graph))
    elif isinstance(node, list):
        for item in node:
            found.extend(_iter_jsonld_objects(item))
    return found


def extract_posting_location_evidence(
    payload: str,
    *,
    content_type: str,
    evidence_url: str,
) -> ExtractedLocationEvidence:
    """Extract explicit geo coordinates or an explicit NSW street address from JSON or HTML."""
    ct = (content_type or "").lower()
    if "json" in ct and "html" not in ct:
        try:
            data = json.loads(payload)
        except ValueError:
            return ExtractedLocationEvidence(None, None, None, "none", evidence_url)
        location = data.get("location") if isinstance(data, dict) else None
        if isinstance(location, dict):
            coords = _coerce_coords(
                location.get("lat") if location.get("lat") is not None else location.get("latitude"),
                location.get("lng") if location.get("lng") is not None else location.get("longitude"),
            )
            street = _format_postal_address(
                {
                    "streetAddress": location.get("street") or location.get("address"),
                    "addressLocality": location.get("city"),
                    "addressRegion": location.get("region") or location.get("state"),
                    "postalCode": location.get("zip_code") or location.get("postalCode"),
                }
            )
            if coords is not None:
                return ExtractedLocationEvidence(coords[0], coords[1], street, "api_geo", evidence_url)
            if street is not None:
                return ExtractedLocationEvidence(None, None, street, "none", evidence_url)
        return ExtractedLocationEvidence(None, None, None, "none", evidence_url)

    for raw_block in _JSONLD_SCRIPT_RE.findall(payload or ""):
        try:
            parsed = json.loads(raw_block.strip())
        except ValueError:
            continue
        for obj in _iter_jsonld_objects(parsed):
            obj_type = obj.get("@type")
            if not (obj_type == "JobPosting" or (isinstance(obj_type, list) and "JobPosting" in obj_type)):
                continue
            job_loc = obj.get("jobLocation")
            locations = job_loc if isinstance(job_loc, list) else ([job_loc] if isinstance(job_loc, dict) else [])
            for loc in locations:
                if not isinstance(loc, dict):
                    continue
                geo = loc.get("geo") if isinstance(loc.get("geo"), dict) else {}
                coords = _coerce_coords(geo.get("latitude"), geo.get("longitude"))
                street = _format_postal_address(loc.get("address"))
                if coords is not None:
                    return ExtractedLocationEvidence(coords[0], coords[1], street, "jsonld_geo", evidence_url)
                if street is not None:
                    return ExtractedLocationEvidence(None, None, street, "none", evidence_url)
    return ExtractedLocationEvidence(None, None, None, "none", evidence_url)


def _pace(min_interval_seconds: float) -> None:
    global _LAST_REQUEST_AT
    if min_interval_seconds <= 0:
        return
    with _LOCK:
        wait = min_interval_seconds - (time.monotonic() - _LAST_REQUEST_AT)
        if wait > 0:
            time.sleep(wait)
        _LAST_REQUEST_AT = time.monotonic()


def geocode_explicit_street_address(
    street_address: str,
    *,
    client: httpx.Client,
    tfnsw_api_key: str | None = None,
    enabled: bool = False,
    min_interval_seconds: float = 1.0,
) -> tuple[float, float] | None:
    """Geocode only an explicit NSW street address via free endpoints (AU$0); never fabricate centroids."""
    if not enabled or not is_explicit_nsw_street_address(street_address):
        return None
    query = " ".join(street_address.split()).strip()
    _pace(min_interval_seconds)
    try:
        if tfnsw_api_key:
            resp = client.get(
                "https://api.transport.nsw.gov.au/v1/tp/stop_finder",
                params={
                    "outputFormat": "rapidJSON",
                    "coordOutputFormat": "EPSG:4326",
                    "type_sf": "coord",
                    "name_sf": query,
                    "anyMaxSizeHitList": 5,
                },
                headers={"Authorization": f"apikey {tfnsw_api_key}"},
                timeout=8.0,
                follow_redirects=False,
            )
            if resp.status_code != 200:
                return None
            for item in resp.json().get("locations", []):
                coord = item.get("coord") if isinstance(item, dict) else None
                if isinstance(coord, list) and len(coord) >= 2:
                    checked = _coerce_coords(coord[0], coord[1])
                    if checked is not None:
                        return checked
            return None

        resp = client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{query}, Australia", "format": "jsonv2", "limit": 1, "countrycodes": "au"},
            headers={"User-Agent": "SydneyStays-PublishedJobs/1.0 (localhost research)"},
            timeout=8.0,
            follow_redirects=False,
        )
        if resp.status_code != 200:
            return None
        rows = resp.json()
        if isinstance(rows, list) and rows:
            first = rows[0] if isinstance(rows[0], dict) else {}
            return _coerce_coords(first.get("lat"), first.get("lon"))
    except (httpx.HTTPError, ValueError):
        return None
    return None
