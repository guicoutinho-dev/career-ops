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


def test_loads_jobs_settings_without_facebook_fields_and_refuses_non_local(tmp_path):
    from hub.settings import JobsConfig, load_jobs_settings

    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "jobs.example.yml").write_text(
        "server:\n  host: 127.0.0.1\n  port: 8796\npublished_jobs:\n  db_path: data/jobs_isolated/jobs_hub.db\n",
        encoding="utf-8",
    )
    cfg = load_jobs_settings(cfg_dir / "jobs.yml")
    assert isinstance(cfg, JobsConfig)
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8796
    assert cfg.published_jobs.db_path == "data/jobs_isolated/jobs_hub.db"
    assert cfg.published_jobs.isolated_db_dir == "data/jobs_isolated"
    assert cfg.published_jobs.scheduler_enabled is False
    assert cfg.published_jobs.adzuna_evaluate_only is True
    assert cfg.published_jobs.geocode_street_addresses is False

    with pytest.raises(ValueError):
        JobsConfig(server={"host": "0.0.0.0", "port": 8796})


def test_jobs_config_anchors_isolated_db_and_rejects_absolute_traversal_or_symlink(tmp_path):
    from hub.settings import JobsConfig

    outside = tmp_path / "outside_dir"
    outside.mkdir()

    # Absolute isolated_db_dir / db_path outside checkout anchor must be refused
    for bad_published in (
        {"isolated_db_dir": r"D:\AntigravityProjetos\career-ops\sydney-hub\data", "db_path": "data/jobs_isolated/jobs_hub.db"},
        {"isolated_db_dir": "data/jobs_isolated", "db_path": r"C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops\sydney-hub\data\hub.db"},
        {"isolated_db_dir": "../outside_dir", "db_path": "data/jobs_isolated/jobs_hub.db"},
        {"isolated_db_dir": "data/jobs_isolated", "db_path": "../outside_dir/jobs_hub.db"},
        {"isolated_db_dir": "data", "db_path": "data/hub.db"},
        {"isolated_db_dir": "data/jobs_isolated", "db_path": "data/jobs_isolated/hub.db"},
    ):
        with pytest.raises(ValueError):
            JobsConfig(published_jobs=bad_published, root=tmp_path)

    # Symlink data/jobs_isolated pointing outside must also be refused when supported
    link_parent = tmp_path / "data"
    link_parent.mkdir(exist_ok=True)
    symlink_dir = link_parent / "jobs_isolated"
    try:
        symlink_dir.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        symlink_dir = None
    if symlink_dir is not None:
        with pytest.raises(ValueError):
            JobsConfig(
                published_jobs={
                    "isolated_db_dir": "data/jobs_isolated",
                    "db_path": "data/jobs_isolated/jobs_hub.db",
                },
                root=tmp_path,
            )

