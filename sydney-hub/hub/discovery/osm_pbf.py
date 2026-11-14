"""Streaming local Geofabrik .osm.pbf employer discovery for the four-suburb pilot."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from hub.discovery.models import DiscoveryBatch, EmployerCandidate

_PILOT_SUBURBS = ("Rockdale", "Kogarah", "Hurstville", "Mascot")
_PILOT_CATEGORIES = ("cafe", "restaurant", "bar", "hotel", "cleaning service", "retail store")
_CENTROIDS = Path(__file__).resolve().parents[2] / "web" / "seed" / "suburb_centroids.json"
_VENDOR = Path(__file__).resolve().parents[2] / "data" / "vendor"
_TAG_KEYS = ("amenity", "tourism", "office", "shop", "craft")


def _import_osmium():
    try:
        import osmium  # type: ignore[import-not-found]
        return osmium
    except ImportError:
        if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
            sys.path.insert(0, str(_VENDOR))
        import osmium  # type: ignore[import-not-found]
        return osmium


def _category_for_tags(tags: object) -> str | None:
    amenity = tags.get("amenity")
    shop = tags.get("shop")
    if amenity == "cafe" or shop in {"bakery", "coffee", "pastry"}:
        return "cafe"
    if amenity in {"restaurant", "fast_food", "bistro", "food_court"}:
        return "restaurant"
    if amenity in {"bar", "pub"}:
        return "bar"
    if tags.get("tourism") in {"hotel", "motel", "hostel", "guest_house"}:
        return "hotel"
    if tags.get("office") == "cleaning" or shop in {"laundry", "dry_cleaning"} or tags.get("craft") == "cleaning":
        return "cleaning service"
    if shop:
        return "retail store"
    return None


def _normalize_website_url(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or any(ch.isspace() for ch in text):
        return None
    if "://" not in text and ":" not in text and "." in text:
        text = f"https://{text}"
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or "." not in (parsed.hostname or ""):
        return None
    return text


def _url_key(url: str) -> str:
    parsed = urlsplit(url)
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=parsed.path.rstrip("/") or "/",
        fragment="",
    ).geturl()


def _assign_suburb(
    lat: float,
    lon: float,
    addr_suburb: str | None,
    boxes: dict[str, dict],
) -> str | None:
    hits = [
        suburb
        for suburb, info in boxes.items()
        if info["bbox"][0] <= lat <= info["bbox"][1] and info["bbox"][2] <= lon <= info["bbox"][3]
    ]
    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]
    if addr_suburb:
        target = addr_suburb.strip().casefold()
        for suburb in hits:
            if suburb.casefold() == target:
                return suburb
    return min(hits, key=lambda suburb: (lat - boxes[suburb]["lat"]) ** 2 + (lon - boxes[suburb]["lng"]) ** 2)


def _extract_tags(tags: object) -> tuple[str | None, str | None, str | None, str | None]:
    category = _category_for_tags(tags)
    if not category:
        return None, None, None, None
    name = (tags.get("name") or "").strip() or None
    website = _normalize_website_url(tags.get("website") or tags.get("contact:website"))
    addr_suburb = (tags.get("addr:suburb") or "").strip() or None
    return category, name, website, addr_suburb


def index_pbf_businesses(
    pbf_path: Path | str,
    *,
    centroids_path: Path = _CENTROIDS,
    suburbs: tuple[str, ...] = _PILOT_SUBURBS,
    categories: tuple[str, ...] = _PILOT_CATEGORIES,
) -> dict[tuple[str, str], DiscoveryBatch]:
    """Stream an .osm.pbf extract and group businesses by (suburb, category)."""
    pbf = Path(pbf_path)
    if not pbf.is_file():
        raise FileNotFoundError(f"OSM PBF file not found: {pbf}")
    if any(s not in _PILOT_SUBURBS for s in suburbs) or any(c not in _PILOT_CATEGORIES for c in categories):
        raise ValueError("Suburb or category is outside this pilot")

    places = json.loads(centroids_path.read_text(encoding="utf-8"))
    boxes = {suburb: places[suburb] for suburb in suburbs}
    osmium = _import_osmium()

    buckets: dict[tuple[str, str], dict] = {
        (suburb, category): {"candidates": [], "total": 0, "missing": 0, "duplicates": 0}
        for suburb in suburbs
        for category in categories
    }
    seen_urls: set[str] = set()
    category_set = set(categories)

    def record(suburb: str, category: str, source_key: str, name: str | None, website: str | None,
               lat: float | None, lng: float | None) -> None:
        bucket = buckets.get((suburb, category))
        if bucket is None:
            return
        bucket["total"] += 1
        if not website:
            bucket["missing"] += 1
            return
        key = _url_key(website)
        if key in seen_urls:
            bucket["duplicates"] += 1
            return
        seen_urls.add(key)
        bucket["candidates"].append(
            EmployerCandidate(
                website_url=website,
                discovery_source="OpenStreetMap discovery",
                suburb=suburb,
                category=category,
                source_key=source_key,
                business_name=name,
                lat=lat,
                lng=lng,
            )
        )

    node_stream = (
        osmium.FileProcessor(str(pbf), osmium.osm.NODE)
        .with_filter(osmium.filter.EmptyTagFilter())
        .with_filter(osmium.filter.KeyFilter(*_TAG_KEYS))
    )
    for node in node_stream:
        category, name, website, addr_suburb = _extract_tags(node.tags)
        if category not in category_set or not node.location.valid():
            continue
        lat, lon = float(node.location.lat), float(node.location.lon)
        suburb = _assign_suburb(lat, lon, addr_suburb, boxes)
        if suburb:
            record(suburb, category, f"node/{node.id}", name, website, lat, lon)

    ways: list[tuple[int, str, str | None, str | None, str | None]] = []
    needed_nodes: dict[int, list[int]] = {}
    way_stream = (
        osmium.FileProcessor(str(pbf), osmium.osm.WAY)
        .with_filter(osmium.filter.EmptyTagFilter())
        .with_filter(osmium.filter.KeyFilter(*_TAG_KEYS))
    )
    for way in way_stream:
        category, name, website, addr_suburb = _extract_tags(way.tags)
        if category not in category_set or len(way.nodes) == 0:
            continue
        idx = len(ways)
        ways.append((way.id, category, name, website, addr_suburb))
        needed_nodes.setdefault(way.nodes[0].ref, []).append(idx)

    if needed_nodes:
        id_filter = osmium.filter.IdFilter(needed_nodes.keys())
        id_filter.enable_for(osmium.osm.NODE)
        for node in osmium.FileProcessor(str(pbf), osmium.osm.NODE).with_filter(id_filter):
            if not node.location.valid():
                continue
            lat, lon = float(node.location.lat), float(node.location.lon)
            for idx in needed_nodes[node.id]:
                wid, category, name, website, addr_suburb = ways[idx]
                suburb = _assign_suburb(lat, lon, addr_suburb, boxes)
                if suburb:
                    record(suburb, category, f"way/{wid}", name, website, lat, lon)

    return {
        key: DiscoveryBatch(
            candidates=data["candidates"],
            requests=0,
            cost_estimate=0.0,
            total_businesses=data["total"],
            missing_websites=data["missing"],
            duplicate_websites=data["duplicates"],
        )
        for key, data in buckets.items()
    }


def discover_businesses(
    suburb: str,
    category: str,
    *,
    pbf_path: Path | str,
    limit: int | None = None,
    centroids_path: Path = _CENTROIDS,
) -> DiscoveryBatch:
    if suburb not in _PILOT_SUBURBS or category not in _PILOT_CATEGORIES:
        raise ValueError("Suburb or category is outside this pilot")
    indexed = index_pbf_businesses(
        pbf_path, centroids_path=centroids_path, suburbs=(suburb,), categories=(category,)
    )
    batch = indexed[(suburb, category)]
    candidates = batch.candidates[:limit] if limit is not None else batch.candidates
    return DiscoveryBatch(
        candidates=candidates,
        requests=batch.requests,
        cost_estimate=batch.cost_estimate,
        total_businesses=batch.total_businesses,
        missing_websites=batch.missing_websites,
        duplicate_websites=batch.duplicate_websites,
    )


def make_pbf_discover(
    pbf_path: Path | str,
    *,
    centroids_path: Path = _CENTROIDS,
    suburbs: tuple[str, ...] = _PILOT_SUBURBS,
    categories: tuple[str, ...] = _PILOT_CATEGORIES,
    limit: int | None = None,
) -> Callable[[str, str, int], DiscoveryBatch]:
    """Build a single-pass discovery callable over a local Geofabrik .osm.pbf extract."""
    indexed = index_pbf_businesses(
        pbf_path, centroids_path=centroids_path, suburbs=suburbs, categories=categories
    )

    def _discover(suburb: str, category: str, requested_limit: int) -> DiscoveryBatch:
        batch = indexed[(suburb, category)]
        cap = limit if limit is not None else requested_limit
        cands = batch.candidates[:cap] if cap and cap > 0 else batch.candidates
        return DiscoveryBatch(
            candidates=cands,
            requests=batch.requests,
            cost_estimate=batch.cost_estimate,
            total_businesses=batch.total_businesses,
            missing_websites=batch.missing_websites,
            duplicate_websites=batch.duplicate_websites,
        )

    return _discover
