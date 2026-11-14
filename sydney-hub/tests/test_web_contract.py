from pathlib import Path


WEB = Path(__file__).parents[1] / "web" / "template.html"


def test_automation_matches_backend_contract():
    html = WEB.read_text(encoding="utf-8")
    assert "/api/groups/${id}/toggle" not in html
    assert "method: 'PATCH'" in html
    assert "Servidor local offline" in html
    assert ".inspector {" in html
    assert "overflow-y: auto" in html
def test_build_dashboard_without_personal_seed_files(tmp_path):
    from web.build_dashboard import build

    (tmp_path / "seed").mkdir()
    (tmp_path / "template.html").write_text(
        "const LISTINGS = __LISTINGS_JSON__; const ENRICHED_JOBS = []; "
        "const SUBURB_GEO = __SUBURB_GEO_JSON__;", encoding="utf-8",
    )
    (tmp_path / "seed" / "suburb_centroids.json").write_text("{}", encoding="utf-8")
    build(tmp_path)
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert "const LISTINGS = [];" in html
    assert "const ENRICHED_JOBS = [];" in html


def test_built_page_never_embeds_legacy_jobs():
    from web.build_dashboard import build
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'seed').mkdir()
        (root / 'template.html').write_text('const LISTINGS = __LISTINGS_JSON__; const SUBURB_GEO = __SUBURB_GEO_JSON__; const ENRICHED_JOBS = [];', encoding='utf-8')
        (root / 'seed' / 'jobs.json').write_text('[{"id":"stale-fake"}]', encoding='utf-8')
        build(root)
        assert 'stale-fake' not in (root / 'index.html').read_text(encoding='utf-8')


def test_template_has_no_candidate_identity_or_unverified_pitch():
    from pathlib import Path

    template = (Path(__file__).resolve().parents[1] / "web" / "template.html").read_text(encoding="utf-8")
    assert "Guilherme" not in template
    assert "I have solid experience in fast-paced customer service" not in template
    assert 'Mapeamento executivo de 22+' not in template
    assert 'Cafeteria moderna com vaga aberta' not in template
