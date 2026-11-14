import logging
import re
import time
from dataclasses import dataclass
from typing import List, Optional

from src.models.job import JobCandidate
from src.services.normalizer import compute_content_hash, normalize_location, normalize_url

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class JobSpySourceStatus:
    status: str
    matches: int = 0
    error: str = ""


@dataclass(frozen=True)
class JobSpyScanResult:
    candidates: list[JobCandidate]
    sources: dict[str, JobSpySourceStatus]


class JobSpyAdapter:
    """Adapter for JobSpy multi-portal search configured for Australia."""

    def __init__(self, country: str = "australia", rate_limit_delay: float = 1.0):
        self.country = country
        self.rate_limit_delay = rate_limit_delay

    def search_jobs(
        self,
        search_term: str,
        location: str = "Australia",
        results_wanted: int = 10,
        sites: Optional[List[str]] = None,
        hours_old: Optional[int] = 48,
    ) -> List[JobCandidate]:
        """Compatibility API returning candidates without source diagnostics."""
        return self.scan_jobs(search_term, location, results_wanted, sites, hours_old).candidates

    def scan_jobs(
        self,
        search_term: str,
        location: str = "Australia",
        results_wanted: int = 10,
        sites: Optional[List[str]] = None,
        hours_old: Optional[int] = 48,
    ) -> JobSpyScanResult:
        """Execute a read-only JobSpy search and retain per-board outcomes."""
        requested_sites = [str(site).lower() for site in (sites or ["indeed", "linkedin"])]
        candidates: list[JobCandidate] = []
        try:
            from jobspy import scrape_jobs

            logger.info(
                "Querying JobSpy: term=%r, loc=%r, sites=%s, hours_old=%s",
                search_term,
                location,
                requested_sites,
                hours_old,
            )
            time.sleep(self.rate_limit_delay)
            jobs_df = scrape_jobs(
                site_name=requested_sites,
                search_term=search_term,
                location=location,
                results_wanted=results_wanted,
                country_indeed=self.country,
                hours_old=hours_old,
            )

            if jobs_df is not None and not jobs_df.empty:
                for _, row in jobs_df.iterrows():
                    raw_url = str(row.get("job_url", "") or "")
                    if not raw_url or raw_url == "nan":
                        continue
                    canonical_url = normalize_url(raw_url)
                    title = str(row.get("title", "") or "Untitled")
                    employer = str(row.get("company", "") or "Unknown Company")
                    raw_loc = str(row.get("location", "") or location)
                    normalized_loc = normalize_location(raw_loc)
                    desc = str(row.get("description", "") or "")
                    content_hash = compute_content_hash(f"{employer} {title} {desc[:500]}")
                    job_id = f"jobspy_{content_hash[:16]}"
                    site = str(row.get("site", "unknown") or "unknown").lower()
                    candidates.append(JobCandidate(
                        id=job_id,
                        source=f"jobspy_{site}",
                        external_id=str(row.get("id", "") or "") or None,
                        canonical_url=canonical_url,
                        employer=employer,
                        title=title,
                        location=normalized_loc,
                        description=desc,
                        publication_date=str(row.get("date_posted", "") or "") or None,
                        status="DISCOVERED",
                        content_hash=content_hash,
                    ))

            counts = {site: 0 for site in requested_sites}
            for candidate in candidates:
                site = candidate.source.removeprefix("jobspy_")
                if site in counts:
                    counts[site] += 1
            raw_errors = getattr(jobs_df, "attrs", {}).get("errors", {}) if jobs_df is not None else {}
            if not isinstance(raw_errors, dict):
                raw_errors = {}
            errors = {str(site).lower(): str(message) for site, message in raw_errors.items()}
            statuses = {}
            for site in requested_sites:
                error = errors.get(site, "")
                if not error:
                    status = "searched"
                elif counts[site] > 0:
                    status = "partial"
                elif re.search(r"\b(401|403|429)\b|blocked|captcha|challenge", error, re.IGNORECASE):
                    status = "blocked"
                else:
                    status = "failed"
                statuses[site] = JobSpySourceStatus(status, counts[site], error)
            return JobSpyScanResult(candidates, statuses)
        except Exception as exc:
            logger.error("JobSpy scrape failed: %s", exc, exc_info=True)
            return JobSpyScanResult(
                candidates=[],
                sources={site: JobSpySourceStatus("failed", error=str(exc)) for site in requested_sites},
            )
