"""Read candidate facts only from the canonical user profile."""
from pathlib import Path
from typing import Any

import yaml


def load_candidate_profile(profile_path: Path | str = "config/profile.yml") -> dict[str, Any]:
    """Load the user-layer profile without adding defaults or inferred facts."""
    path = Path(profile_path)
    if not path.is_file():
        return {}
    parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(parsed, dict):
        raise ValueError(f"Candidate profile must be a YAML mapping: {path}")
    return parsed
