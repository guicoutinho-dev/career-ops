import pytest

from hub.settings import Settings, load_settings


def test_loads_project_settings():
    from pathlib import Path
    settings = load_settings(Path(__file__).resolve().parent.parent / "config" / "settings.yml")
    assert settings.schedule == ["08:00", "14:00", "20:00"]
    assert settings.server.host == "127.0.0.1"
    assert settings.resolve(settings.scrape.script_path).name == "scrape-posts.py"
    assert settings.resolve(settings.scrape.script_path).exists()


def test_non_local_host_is_refused():
    with pytest.raises(ValueError):
        Settings(schedule=["08:00"], scrape={"script_path": "x"}, server={"host": "0.0.0.0"})
def test_uses_example_settings_when_personal_settings_missing(tmp_path):
    from hub.settings import load_settings

    example = tmp_path / "settings.example.yml"
    example.write_text("schedule: ['08:00']\nscrape:\n  script_path: ../scrape.py\n", encoding="utf-8")
    settings = load_settings(tmp_path / "settings.yml")
    assert settings.schedule == ["08:00"]
    assert settings.seed_groups == []
