"""Bounded OpenStreetMap website-pointer lookup for the four-suburb pilot."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from hub.discovery.models import DiscoveryBatch, EmployerCandidate

_ENDPOINT = 'https://overpass.private.coffee/api/interpreter'
_CENTROIDS = Path(__file__).resolve().parents[2] / 'web' / 'seed' / 'suburb_centroids.json'
_CATEGORIES = {
    'cafe': ('amenity', 'cafe'),
    'restaurant': ('amenity', 'restaurant'),
    'bar': ('amenity', 'bar|pub'),
    'hotel': ('tourism', 'hotel|guest_house|hostel'),
    'cleaning service': ('office', 'cleaning'),
    'retail store': ('shop', '.+'),
}
_MAX_BYTES = 300_000


def discover_businesses(suburb: str, category: str, *, limit: int, client: httpx.Client) -> DiscoveryBatch:
    places = json.loads(_CENTROIDS.read_text(encoding='utf-8'))
    if suburb not in {'Rockdale', 'Kogarah', 'Hurstville', 'Mascot'} or suburb not in places:
        raise ValueError('Suburb is outside this pilot')
    if category not in _CATEGORIES or not 1 <= limit <= 10:
        raise ValueError('Category or limit is outside this pilot')
    south, north, west, east = places[suburb]['bbox']
    bbox = f'{south},{west},{north},{east}'
    key, value = _CATEGORIES[category]
    selector = f'["{key}"~"^{value}$"]'
    query = ('[out:json][timeout:15];('
             f'nwr{selector}["website"]({bbox});'
             f'nwr{selector}["contact:website"]({bbox});'
             f');out tags {limit};')
    with client.stream('POST', _ENDPOINT, data={'data': query},
                       headers={'User-Agent': 'SydneyStays-local-employer-pilot/0.1 (OSM website lookup)'},
                       timeout=25) as response:
        if response.status_code != 200:
            raise RuntimeError(f'Overpass returned HTTP {response.status_code}')
        chunks, size = [], 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > _MAX_BYTES:
                raise RuntimeError('Overpass response exceeded pilot size limit')
            chunks.append(chunk)
    payload = json.loads(b''.join(chunks))
    candidates, seen = [], set()
    for element in payload.get('elements', []):
        tags = element.get('tags') or {}
        website = tags.get('website') or tags.get('contact:website')
        parsed = urlsplit(website) if isinstance(website, str) else None
        if not parsed or parsed.scheme not in {'http', 'https'} or not parsed.netloc or website in seen:
            continue
        if element.get('type') not in {'node', 'way', 'relation'} or not isinstance(element.get('id'), int):
            continue
        seen.add(website)
        candidates.append(EmployerCandidate(website, 'OpenStreetMap discovery', suburb, category,
                                            f"{element['type']}/{element['id']}"))
        if len(candidates) == limit:
            break
    return DiscoveryBatch(candidates, 1, 0.0)
