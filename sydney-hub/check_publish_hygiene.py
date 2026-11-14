"""Fail-closed pre-publish Git hygiene scanner for SydneyStays / career-ops."""
from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

FORBIDDEN_TRACKED_PATTERNS: tuple[str, ...] = (
    "config/gmail_*",
    ".pii-token-map.json",
    "USER.md",
    "*.db",
    "applications/",
    "data/*.db",
    "sydney-hub/data/",
    "sites/",
)

KNOWN_SENSITIVE_COMMITS: tuple[str, ...] = ("d31174e5", "d42e2d9b")

_HISTORY_PATHSPECS: tuple[str, ...] = (
    ":(glob)config/gmail_*",
    ".pii-token-map.json",
    "USER.md",
    "*.db",
    "applications",
    "sydney-hub/data",
    "sites",
)


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _matches_forbidden(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/")
    while norm.startswith("./"):
        norm = norm[2:]
    if not norm:
        return False
    basename = norm.rsplit("/", 1)[-1]
    for pattern in FORBIDDEN_TRACKED_PATTERNS:
        if pattern.endswith("/"):
            prefix = pattern.rstrip("/")
            if norm == prefix or norm.startswith(prefix + "/"):
                return True
        elif "/" in pattern:
            if fnmatch.fnmatch(norm, pattern):
                return True
        else:
            if fnmatch.fnmatch(norm, pattern) or fnmatch.fnmatch(basename, pattern):
                return True
    return False


def _scan_tracked_tree(repo_root: Path, git_ref: str) -> list[str]:
    proc = _git(repo_root, "ls-tree", "-r", "--name-only", "-z", git_ref)
    if proc.returncode != 0:
        raise RuntimeError(f"Unable to list git tree for {git_ref}: {proc.stderr.strip()}")
    paths = [p for p in proc.stdout.split("\0") if p]
    return sorted({p for p in paths if _matches_forbidden(p)})


def _scan_dirty_worktree(repo_root: Path) -> list[str]:
    proc = _git(repo_root, "status", "--porcelain=v1", "-z", "-uall")
    if proc.returncode != 0:
        return []
    entries = proc.stdout.split("\0")
    flagged: set[str] = set()
    idx = 0
    while idx < len(entries):
        entry = entries[idx]
        idx += 1
        if len(entry) < 4:
            continue
        status = entry[:2]
        path = entry[3:]
        if _matches_forbidden(path):
            flagged.add(path)
        if status[0] in ("R", "C") and idx < len(entries):
            orig = entries[idx]
            idx += 1
            if _matches_forbidden(orig):
                flagged.add(orig)
    return sorted(flagged)


def _scan_sensitive_history(repo_root: Path, git_ref: str) -> list[str]:
    hits: set[str] = set()
    for commit_prefix in KNOWN_SENSITIVE_COMMITS:
        rev = _git(repo_root, "rev-parse", "--verify", "--quiet", f"{commit_prefix}^{{commit}}")
        if rev.returncode != 0:
            continue
        full_sha = rev.stdout.strip()
        anc = _git(repo_root, "merge-base", "--is-ancestor", full_sha, git_ref)
        if anc.returncode == 0:
            hits.add(full_sha)

    log_proc = _git(
        repo_root,
        "log",
        git_ref,
        "--format=%H",
        "-n",
        "50",
        "--",
        *_HISTORY_PATHSPECS,
    )
    if log_proc.returncode == 0:
        for line in log_proc.stdout.splitlines():
            sha = line.strip()
            if sha:
                hits.add(sha)
    return sorted(hits)


def check_tree_hygiene(repo_root: Path, git_ref: str = "HEAD") -> dict[str, object]:
    """Audit tracked tree, dirty paths, and reachable history without reading file contents."""
    repo = Path(repo_root).resolve()
    forbidden_tracked = _scan_tracked_tree(repo, git_ref)
    dirty_sensitive = _scan_dirty_worktree(repo)
    sensitive_history_commits = _scan_sensitive_history(repo, git_ref)
    is_safe = not forbidden_tracked and not dirty_sensitive and not sensitive_history_commits
    return {
        "git_ref": git_ref,
        "forbidden_tracked": forbidden_tracked,
        "sensitive_history_commits": sensitive_history_commits,
        "dirty_sensitive": dirty_sensitive,
        "is_safe_to_publish": is_safe,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail-closed pre-publish Git credential/PII hygiene gate")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ref", default="HEAD")
    args = parser.parse_args(argv)

    report = check_tree_hygiene(args.repo, git_ref=args.ref)
    print(json.dumps(report, indent=2))
    return 0 if report["is_safe_to_publish"] else 1


if __name__ == "__main__":
    sys.exit(main())
