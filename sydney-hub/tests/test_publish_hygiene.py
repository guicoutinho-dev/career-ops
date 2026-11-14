import subprocess
from pathlib import Path
import pytest
from check_publish_hygiene import check_tree_hygiene, FORBIDDEN_TRACKED_PATTERNS


def test_hygiene_flags_forbidden_paths_and_d31174e5_ancestor(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "config").mkdir()
    (repo / "config" / "gmail_token.json").write_text("dummy", encoding="utf-8")
    subprocess.run(["git", "add", "config/gmail_token.json"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "bad"], cwd=repo, check=True, capture_output=True)

    report = check_tree_hygiene(repo, git_ref="HEAD")
    assert "config/gmail_token.json" in report["forbidden_tracked"]
    assert len(report["sensitive_history_commits"]) >= 1
    assert report["is_safe_to_publish"] is False

    # Even if deleted in a follow-up commit, history remains flagged
    subprocess.run(["git", "rm", "--cached", "config/gmail_token.json"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "remove from tip"], cwd=repo, check=True, capture_output=True)
    report_after_rm = check_tree_hygiene(repo, git_ref="HEAD")
    assert report_after_rm["forbidden_tracked"] == []
    assert len(report_after_rm["sensitive_history_commits"]) >= 1
    assert report_after_rm["is_safe_to_publish"] is False


def test_hygiene_passes_clean_snapshot_repo(tmp_path: Path):
    repo = tmp_path / "clean"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "README.md").write_text("clean", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "clean"], cwd=repo, check=True, capture_output=True)

    report = check_tree_hygiene(repo, git_ref="HEAD")
    assert report["forbidden_tracked"] == []
    assert report["sensitive_history_commits"] == []
    assert report["is_safe_to_publish"] is True


def test_hygiene_flags_pii_db_and_dirty_sensitive_without_leaking_contents(tmp_path: Path):
    repo = tmp_path / "pii"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "README.md").write_text("ok", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    (repo / ".pii-token-map.json").write_text('{"secret_key": "DO_NOT_LEAK"}', encoding="utf-8")
    (repo / "sydney-hub" / "data").mkdir(parents=True)
    (repo / "sydney-hub" / "data" / "hub.db").write_bytes(b"sqlite-dummy")
    subprocess.run(["git", "add", ".pii-token-map.json", "sydney-hub/data/hub.db"], cwd=repo, check=True)

    report = check_tree_hygiene(repo, git_ref="HEAD")
    assert ".pii-token-map.json" in report["dirty_sensitive"]
    assert "sydney-hub/data/hub.db" in report["dirty_sensitive"]
    assert report["is_safe_to_publish"] is False
    assert "DO_NOT_LEAK" not in str(report)
    assert any("gmail" in p for p in FORBIDDEN_TRACKED_PATTERNS)


def test_cli_fails_closed_on_sensitive_history_and_rejects_bypass_flag(tmp_path: Path):
    import json
    import sys

    repo = tmp_path / "cli_hist"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "config").mkdir()
    (repo / "config" / "gmail_token.json").write_text("dummy", encoding="utf-8")
    subprocess.run(["git", "add", "config/gmail_token.json"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "bad"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "rm", "--cached", "config/gmail_token.json"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "clean tip"], cwd=repo, check=True, capture_output=True)

    hub_root = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, str(hub_root / "check_publish_hygiene.py"), "--repo", str(repo), "--ref", "HEAD"],
        cwd=hub_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["is_safe_to_publish"] is False
    assert len(payload["sensitive_history_commits"]) >= 1

    bypass_proc = subprocess.run(
        [
            sys.executable,
            str(hub_root / "check_publish_hygiene.py"),
            "--repo",
            str(repo),
            "--ref",
            "HEAD",
            "--allow-history-ancestor-only",
        ],
        cwd=hub_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert bypass_proc.returncode != 0


def test_hygiene_flags_arbitrary_config_gmail_wildcard_in_tree_history_and_dirty(tmp_path: Path):
    repo = tmp_path / "gmail_wild"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "README.md").write_text("init", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    (repo / "config").mkdir()
    new_secret = repo / "config" / "gmail_oauth_secret_custom.json"
    new_secret.write_text("secret", encoding="utf-8")

    dirty_report = check_tree_hygiene(repo, git_ref="HEAD")
    assert "config/gmail_oauth_secret_custom.json" in dirty_report["dirty_sensitive"]
    assert dirty_report["is_safe_to_publish"] is False

    subprocess.run(["git", "add", "config/gmail_oauth_secret_custom.json"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "add custom gmail file"], cwd=repo, check=True, capture_output=True)

    tracked_report = check_tree_hygiene(repo, git_ref="HEAD")
    assert "config/gmail_oauth_secret_custom.json" in tracked_report["forbidden_tracked"]
    assert len(tracked_report["sensitive_history_commits"]) >= 1
    assert tracked_report["is_safe_to_publish"] is False

    subprocess.run(["git", "rm", "--cached", "config/gmail_oauth_secret_custom.json"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "remove custom gmail file"], cwd=repo, check=True, capture_output=True)

    history_report = check_tree_hygiene(repo, git_ref="HEAD")
    assert history_report["forbidden_tracked"] == []
    assert len(history_report["sensitive_history_commits"]) >= 1
    assert history_report["is_safe_to_publish"] is False
