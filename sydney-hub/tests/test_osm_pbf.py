import sys
from pathlib import Path

import pytest

_VENDOR = Path(__file__).resolve().parents[1] / "data" / "vendor"
if _VENDOR.is_dir() and str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

osmium = pytest.importorskip("osmium")


def _write_sample_pbf(path: Path) -> Path:
    writer = osmium.SimpleWriter(str(path))
    try:
        # 1. Rockdale cafe with full tags
        writer.add_node(osmium.osm.mutable.Node(
            id=101,
            location=(151.1403, -33.9542),
            tags={"amenity": "cafe", "name": "Rockdale Espresso", "website": "https://rockdale.example/"},
        ))
        # 2. Hurstville shop with bare www website (normalized to https://)
        writer.add_node(osmium.osm.mutable.Node(
            id=102,
            location=(151.1003, -33.9607),
            tags={"shop": "bakery", "name": "Hurstville Bake", "website": "www.bake.example"},
        ))
        # 3. Kogarah hotel with missing name
        writer.add_node(osmium.osm.mutable.Node(
            id=103,
            location=(151.1354, -33.9682),
            tags={"tourism": "hotel", "website": "https://kogarahhotel.example/"},
        ))
        # 4. Rockdale restaurant missing website
        writer.add_node(osmium.osm.mutable.Node(
            id=104,
            location=(151.1410, -33.9545),
            tags={"amenity": "restaurant", "name": "No Site Bistro"},
        ))
        # 5. Rockdale shop with unsafe javascript: URL (counted as missing website)
        writer.add_node(osmium.osm.mutable.Node(
            id=105,
            location=(151.1415, -33.9548),
            tags={"shop": "convenience", "name": "Unsafe Site", "website": "javascript:alert(1)"},
        ))
        # 6. Duplicate website in Hurstville
        writer.add_node(osmium.osm.mutable.Node(
            id=106,
            location=(151.1008, -33.9609),
            tags={"amenity": "cafe", "name": "Hurstville Bake II", "website": "https://www.bake.example/"},
        ))
        # 7. Overlap bbox (Rockdale/Kogarah) with explicit addr:suburb=Rockdale despite closer to Kogarah
        writer.add_node(osmium.osm.mutable.Node(
            id=107,
            location=(151.1330, -33.9635),
            tags={"amenity": "bar", "name": "Border Bar", "addr:suburb": "Rockdale", "website": "https://borderbar.example/"},
        ))
        # 8. Overlap bbox without addr:suburb -> nearest centroid (Kogarah)
        writer.add_node(osmium.osm.mutable.Node(
            id=108,
            location=(151.1332, -33.9636),
            tags={"amenity": "restaurant", "name": "Kogarah Border Diner", "website": "https://kogarahdiner.example/"},
        ))
        # 9. Outside all four pilot suburbs (Sydney CBD)
        writer.add_node(osmium.osm.mutable.Node(
            id=109,
            location=(151.2093, -33.8688),
            tags={"amenity": "cafe", "name": "CBD Cafe", "website": "https://cbd.example/"},
        ))
        # 10. Way entity in Mascot resolved via node ref
        writer.add_node(osmium.osm.mutable.Node(id=2010, location=(151.1951, -33.9289)))
        writer.add_way(osmium.osm.mutable.Way(
            id=201,
            nodes=[2010],
            tags={"amenity": "pub", "name": "Mascot Pub", "contact:website": "https://mascotpub.example/"},
        ))
    finally:
        writer.close()
    return path


def test_extracts_businesses_from_pilot_suburbs_with_counts_and_coordinates(tmp_path):
    from hub.discovery.osm_pbf import discover_businesses, index_pbf_businesses

    pbf = _write_sample_pbf(tmp_path / "sample.osm.pbf")
    indexed = index_pbf_businesses(pbf)

    rockdale_cafe = indexed[("Rockdale", "cafe")]
    assert rockdale_cafe.requests == 0 and rockdale_cafe.cost_estimate == 0.0
    assert rockdale_cafe.total_businesses == 1
    assert rockdale_cafe.missing_websites == 0
    cand = rockdale_cafe.candidates[0]
    assert cand.business_name == "Rockdale Espresso"
    assert cand.website_url == "https://rockdale.example/"
    assert cand.category == "cafe"
    assert cand.suburb == "Rockdale"
    assert cand.source_key == "node/101"
    assert cand.discovery_source == "OpenStreetMap discovery"
    assert abs(cand.lat - (-33.9542)) < 1e-4 and abs(cand.lng - 151.1403) < 1e-4

    # Missing & unsafe websites counted without polluting candidates
    rockdale_rest = indexed[("Rockdale", "restaurant")]
    assert rockdale_rest.total_businesses == 1 and rockdale_rest.missing_websites == 1 and rockdale_rest.candidates == []
    rockdale_shop = indexed[("Rockdale", "retail store")]
    assert rockdale_shop.total_businesses == 1 and rockdale_shop.missing_websites == 1 and rockdale_shop.candidates == []

    # Kogarah unnamed hotel keeps business_name=None
    kogarah_hotel = indexed[("Kogarah", "hotel")]
    assert len(kogarah_hotel.candidates) == 1
    assert kogarah_hotel.candidates[0].business_name is None

    # Way entity resolved with coordinates
    mascot_bar = indexed[("Mascot", "bar")]
    assert len(mascot_bar.candidates) == 1
    assert mascot_bar.candidates[0].source_key == "way/201"
    assert mascot_bar.candidates[0].business_name == "Mascot Pub"
    assert mascot_bar.candidates[0].lat is not None and mascot_bar.candidates[0].lng is not None

    # Overlap disambiguation and duplicate URL counting
    assert [c.business_name for c in indexed[("Rockdale", "bar")].candidates] == ["Border Bar"]
    assert [c.business_name for c in indexed[("Kogarah", "restaurant")].candidates] == ["Kogarah Border Diner"]

    hurstville_cafe = indexed[("Hurstville", "cafe")]
    assert hurstville_cafe.candidates[0].website_url == "https://www.bake.example"
    assert hurstville_cafe.duplicate_websites == 1

    rockdale_direct = discover_businesses("Rockdale", "cafe", pbf_path=pbf)
    assert [c.business_name for c in rockdale_direct.candidates] == ["Rockdale Espresso"]

    # CBD outside bbox excluded everywhere
    all_urls = {c.website_url for batch in indexed.values() for c in batch.candidates}
    assert "https://cbd.example/" not in all_urls


def test_missing_pbf_and_invalid_args_raise(tmp_path):
    from hub.discovery.osm_pbf import discover_businesses, index_pbf_businesses

    pbf = _write_sample_pbf(tmp_path / "sample.osm.pbf")
    with pytest.raises(FileNotFoundError):
        index_pbf_businesses(tmp_path / "does-not-exist.osm.pbf")
    with pytest.raises(ValueError):
        discover_businesses("Bondi", "cafe", pbf_path=pbf)
    with pytest.raises(ValueError):
        discover_businesses("Rockdale", "factory", pbf_path=pbf)


def test_settings_supports_osm_pbf_with_safe_defaults():
    from hub.settings import EmployerDiscoverySettings

    default = EmployerDiscoverySettings()
    assert default.enabled is False and default.provider == "osm_overpass" and default.max_requests == 80
    pbf_cfg = EmployerDiscoverySettings(
        enabled=True,
        provider="osm_pbf",
        pbf_path="data/osm/new-south-wales-latest.osm.pbf",
        max_requests=1000,
    )
    assert pbf_cfg.provider == "osm_pbf" and pbf_cfg.max_requests == 1000


def test_expanded_hospitality_and_cleaning_osm_tags(tmp_path):
    from hub.discovery.osm_pbf import index_pbf_businesses

    path = tmp_path / "expanded.osm.pbf"
    writer = osmium.SimpleWriter(str(path))
    try:
        writer.add_node(osmium.osm.mutable.Node(
            id=501, location=(151.1403, -33.9542),
            tags={"amenity": "fast_food", "name": "Rockdale Kebabs", "website": "https://kebabs.example/"},
        ))
        writer.add_node(osmium.osm.mutable.Node(
            id=502, location=(151.1003, -33.9607),
            tags={"shop": "bakery", "name": "Hurstville Bakehouse", "website": "https://bakehouse.example/"},
        ))
        writer.add_node(osmium.osm.mutable.Node(
            id=503, location=(151.1354, -33.9682),
            tags={"tourism": "motel", "name": "Kogarah Motel", "website": "https://motel.example/"},
        ))
        writer.add_node(osmium.osm.mutable.Node(
            id=504, location=(151.1951, -33.9289),
            tags={"shop": "laundry", "name": "Mascot Laundry", "website": "https://laundry.example/"},
        ))
        writer.add_node(osmium.osm.mutable.Node(
            id=505, location=(151.1952, -33.9288),
            tags={"craft": "cleaning", "name": "Mascot Sparkle", "website": "https://sparkle.example/"},
        ))
    finally:
        writer.close()

    indexed = index_pbf_businesses(path)
    assert [c.business_name for c in indexed[("Rockdale", "restaurant")].candidates] == ["Rockdale Kebabs"]
    assert [c.business_name for c in indexed[("Hurstville", "cafe")].candidates] == ["Hurstville Bakehouse"]
    assert [c.business_name for c in indexed[("Kogarah", "hotel")].candidates] == ["Kogarah Motel"]
    assert sorted(c.business_name for c in indexed[("Mascot", "cleaning service")].candidates) == [
        "Mascot Laundry", "Mascot Sparkle",
    ]
