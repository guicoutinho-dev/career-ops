"""Minimal .env loader (no external dependency). Existing environment variables win."""

import os
from pathlib import Path


def load_env(path: Path | str = ".env") -> int:
    """Loads KEY=VALUE pairs into os.environ. Returns the number of variables set."""
    env_path = Path(path)
    if not env_path.exists():
        repo_env = Path(__file__).resolve().parent.parent.parent / ".env"
        if repo_env.exists():
            env_path = repo_env
        else:
            return 0
    count = 0
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            count += 1
    return count
