"""Build SydneyStays from the checked-in template and seed data."""
import json
from pathlib import Path

web = Path(__file__).resolve().parent


def build(web_dir: Path = web):
    html = (web_dir / "template.html").read_text(encoding="utf-8")
    for constant, seed in (
        ("LISTINGS", "listings.json"),
        ("SUBURB_GEO", "suburb_centroids.json"),
    ):
        marker = "__" + constant + "_JSON__"
        assert html.count(marker) == 1, f"Expected one {marker} in template"
        source = web_dir / "seed" / seed
        data = json.loads(source.read_text(encoding="utf-8")) if source.exists() else []
        html = html.replace(marker, json.dumps(data, ensure_ascii=False))
    assert "__LISTINGS_JSON__" not in html
    (web_dir / "index.html").write_text(html, encoding="utf-8")
    print(f"SydneyStays generated: {web_dir / 'index.html'} ({len(html):,} characters)")


if __name__ == "__main__":
    build()
