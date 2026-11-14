from pathlib import Path
from typing import Any

import yaml

DEFAULT_SEARCH_PROFILES = Path("config/search_profiles.yaml")
VALID_ROLE_TRACKS = {"sap", "operational"}


def load_search_profiles(config_path: Path | str = DEFAULT_SEARCH_PROFILES) -> dict[str, dict[str, Any]]:
    """Load enabled, validated search profiles without modifying the source file."""
    path = Path(config_path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw_profiles = data.get("profiles") if isinstance(data, dict) else None
    if not isinstance(raw_profiles, dict):
        raise ValueError("search profile config must contain a profiles mapping")

    profiles: dict[str, dict[str, Any]] = {}
    for name, raw_profile in raw_profiles.items():
        if not isinstance(raw_profile, dict):
            raise ValueError(f"profile {name!r} must be a mapping")
        if raw_profile.get("enabled", True) is False:
            continue

        role_track = raw_profile.get("role_track")
        if role_track not in VALID_ROLE_TRACKS:
            raise ValueError(f"profile {name!r} has unsupported role_track {role_track!r}")

        keywords = raw_profile.get("keywords")
        if not isinstance(keywords, dict):
            raise ValueError(f"profile {name!r} must define at least one positive keyword")
        positive = keywords.get("positive")
        if not isinstance(positive, list) or not any(isinstance(term, str) and term.strip() for term in positive):
            raise ValueError(f"profile {name!r} must define at least one positive keyword")

        profile: dict[str, Any] = dict(raw_profile)
        profile["keywords"] = {
            **keywords,
            "positive": [term.strip() for term in positive if isinstance(term, str) and term.strip()],
        }
        geography = raw_profile.get("geography") or {}
        if not isinstance(geography, dict):
            raise ValueError(f"profile {name!r} geography must be a mapping")
        locations = geography.get("locations", [])
        if locations is None:
            locations = []
        if not isinstance(locations, list):
            raise ValueError(f"profile {name!r} geography.locations must be a list")
        profile["geography"] = {
            **geography,
            "locations": [location.strip() for location in locations if isinstance(location, str) and location.strip()],
        }
        profiles[str(name)] = profile

    return profiles


def collect_search_terms(profiles: dict[str, dict[str, Any]]) -> list[str]:
    """Return a stable, case-insensitively unique union of profile title keywords."""
    terms: list[str] = []
    seen: set[str] = set()
    for profile in profiles.values():
        for term in profile["keywords"]["positive"]:
            key = term.casefold()
            if key not in seen:
                seen.add(key)
                terms.append(term)
    return terms
