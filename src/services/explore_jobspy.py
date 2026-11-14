from pathlib import Path
from typing import Any

import yaml

SUPPORTED_SITES = {"indeed", "linkedin", "glassdoor", "zip_recruiter", "bayt", "naukri", "bdjobs", "hellowork"}
SEEK_BLOCKED_REASON = "SEEK API access requires SEEK approval; no request was made."
JOBSPY_POLICY_REASONS = {
    "indeed": "Indeed Australia robots.txt disallows its /jobs/AU search path; no request was made.",
    "linkedin": "LinkedIn robots.txt prohibits automated access without express permission; no request was made.",
}


def run_explore_jobspy(
    filters: dict[str, Any],
    *,
    config_path: Path | str = Path("config/sources.yaml"),
) -> dict[str, Any]:
    """Report external-source availability without querying unapproved job boards."""
    path = Path(config_path)
    raw_config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    sources = raw_config.get("sources", {}) if isinstance(raw_config, dict) else {}
    jobspy_config = sources.get("jobspy", {}) if isinstance(sources, dict) else {}
    seek_config = sources.get("seek_native", {}) if isinstance(sources, dict) else {}
    configured_sites = jobspy_config.get("sites", ["indeed", "linkedin"]) if isinstance(jobspy_config, dict) else ["indeed", "linkedin"]
    if not isinstance(configured_sites, list):
        configured_sites = ["indeed", "linkedin"]
    sites = list(dict.fromkeys(str(site).strip().lower() for site in configured_sites if str(site).strip()))
    sites = [site for site in sites if site in SUPPORTED_SITES] or ["indeed", "linkedin"]

    if isinstance(jobspy_config, dict) and jobspy_config.get("enabled", False):
        jobspy_status = {
            f"jobspy_{site}": {
                "status": "blocked",
                "matches": 0,
                "error": JOBSPY_POLICY_REASONS.get(
                    site,
                    "No robots/permission review exists for this JobSpy board; no request was made.",
                ),
            }
            for site in sites
        }
    else:
        jobspy_status = {
            f"jobspy_{site}": {
                "status": "disabled",
                "matches": 0,
                "error": "JobSpy is disabled in config/sources.yaml.",
            }
            for site in sites
        }

    seek_enabled = isinstance(seek_config, dict) and seek_config.get("enabled", False)
    seek_status = {
        "status": "blocked" if seek_enabled else "disabled",
        "matches": 0,
        "error": SEEK_BLOCKED_REASON if seek_enabled else "SEEK is disabled in config/sources.yaml.",
    }
    return {"offers": [], "sources": {**jobspy_status, "seek_native": seek_status}, "truncated": False}
