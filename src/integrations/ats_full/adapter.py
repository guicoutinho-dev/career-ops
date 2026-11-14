import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from src.models.job import JobCandidate
from src.services.normalizer import compute_content_hash, normalize_location, normalize_url

SUPPORTED_ATS = ("greenhouse", "lever", "ashby", "workday")


@dataclass(frozen=True)
class ATSScanResult:
    candidates: list[JobCandidate]
    summary: dict[str, Any]


class ATSAggregatorAdapter:
    """Read-only wrapper around Career-Ops' public reverse-ATS scanner."""

    def __init__(
        self,
        project_root: Path | str | None = None,
        node_binary: str = "node",
        timeout_seconds: int = 230,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ):
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]
        self.node_binary = node_binary
        self.timeout_seconds = timeout_seconds
        self.runner = runner

    def scan(
        self,
        positive: list[str],
        negative: list[str] | None = None,
        allow_locations: list[str] | None = None,
        block_locations: list[str] | None = None,
        ats: list[str] | None = None,
        since_days: int = 7,
        limit_per_ats: int = 150,
    ) -> ATSScanResult:
        scanner = self.project_root / "scan-ats-full.mjs"
        if not scanner.is_file():
            raise FileNotFoundError(f"ATS scanner not found: {scanner}")

        sources = [source for source in (ats or SUPPORTED_ATS) if source in SUPPORTED_ATS]
        if not sources:
            raise ValueError("At least one supported ATS source is required")
        terms = [term.strip() for term in positive if isinstance(term, str) and term.strip()]
        if not terms:
            raise ValueError("At least one positive search term is required")

        portals: dict[str, Any] = {"title_filter": {"positive": terms}}
        exclusions = [term.strip() for term in (negative or []) if isinstance(term, str) and term.strip()]
        if exclusions:
            portals["title_filter"]["negative"] = exclusions
        allowed = [term.strip() for term in (allow_locations or []) if isinstance(term, str) and term.strip()]
        blocked = [term.strip() for term in (block_locations or []) if isinstance(term, str) and term.strip()]
        if allowed or blocked:
            portals["location_filter"] = {}
            if allowed:
                portals["location_filter"]["allow"] = allowed
            if blocked:
                portals["location_filter"]["block"] = blocked

        temp_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".yml", prefix="career-ops-ats-", delete=False
            ) as temp_file:
                yaml.safe_dump(portals, temp_file, sort_keys=False, allow_unicode=True)
                temp_path = temp_file.name

            command = [
                self.node_binary,
                str(scanner),
                "--dry-run",
                "--json",
                "--since",
                str(max(1, int(since_days))),
                "--ats",
                ",".join(sources),
                "--limit",
                str(max(1, int(limit_per_ats))),
                "--shuffle",
            ]
            env = {**__import__("os").environ, "CAREER_OPS_PORTALS": temp_path}
            completed = self.runner(
                command,
                cwd=self.project_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "no diagnostic output").strip()
                raise RuntimeError(f"ATS scanner exited {completed.returncode}: {detail}")
            try:
                payload = json.loads(completed.stdout)
            except (TypeError, json.JSONDecodeError) as exc:
                raise RuntimeError("ATS scanner returned invalid JSON") from exc
            if not isinstance(payload, dict) or not isinstance(payload.get("offers"), list):
                raise RuntimeError("ATS scanner JSON is missing its offers list")

            candidates = [self._candidate(offer) for offer in payload["offers"]]
            candidates = [candidate for candidate in candidates if candidate is not None]
            return ATSScanResult(candidates=candidates, summary=payload)
        finally:
            if temp_path:
                Path(temp_path).unlink(missing_ok=True)

    def scan_profiles(
        self,
        profiles: dict[str, dict[str, Any]],
        ats: list[str] | None = None,
        since_days: int = 7,
        limit_per_ats: int = 150,
    ) -> ATSScanResult:
        """Scan each profile with its own terms and geography under one per-ATS budget."""
        if not profiles:
            raise ValueError("At least one enabled search profile is required")
        budget = max(1, int(limit_per_ats))
        if budget < len(profiles):
            raise ValueError(
                f"limit_per_ats ({budget}) must cover all {len(profiles)} enabled search profiles"
            )

        base, remainder = divmod(budget, len(profiles))
        results: list[ATSScanResult] = []
        for index, profile in enumerate(profiles.values()):
            keywords = profile["keywords"]
            geography = profile.get("geography") or {}
            results.append(
                self.scan(
                    positive=keywords["positive"],
                    negative=keywords.get("negative", []),
                    allow_locations=geography.get("locations", []),
                    ats=ats,
                    since_days=since_days,
                    limit_per_ats=base + (1 if index < remainder else 0),
                )
            )

        return self._merge_profile_scan_results(results)

    @staticmethod
    def _merge_profile_scan_results(results: list[ATSScanResult]) -> ATSScanResult:
        candidates = [candidate for result in results for candidate in result.candidates]
        summaries = [result.summary for result in results]
        sources = list(dict.fromkeys(source for summary in summaries for source in summary.get("sources", [])))
        dataset_status_values: dict[str, list[str]] = {}
        source_stat_values: dict[str, list[dict[str, Any]]] = {}
        for summary in summaries:
            for source, status in summary.get("datasetStatus", {}).items():
                dataset_status_values.setdefault(source, []).append(str(status))
            for source, stats in summary.get("sourceStats", {}).items():
                source_stat_values.setdefault(source, []).append(stats)

        def merged_status(values: list[str]) -> str:
            return "ok" if values and all(value == "ok" for value in values) else next(
                (value for value in values if value != "ok"), "unknown"
            )

        dataset_status = {source: merged_status(values) for source, values in dataset_status_values.items()}
        source_stats: dict[str, dict[str, Any]] = {}
        for source, entries in source_stat_values.items():
            source_stats[source] = {
                "datasetStatus": merged_status([str(entry.get("datasetStatus", "unknown")) for entry in entries]),
                "companiesAvailable": max((int(entry.get("companiesAvailable", 0)) for entry in entries), default=0),
                "companiesScanned": sum(int(entry.get("companiesScanned", 0)) for entry in entries),
                "unreachableBoards": sum(int(entry.get("unreachableBoards", 0)) for entry in entries),
                "matches": sum(int(entry.get("matches", 0)) for entry in entries),
                "postingsDroppedNoDate": sum(int(entry.get("postingsDroppedNoDate", 0)) for entry in entries),
            }

        summary = {
            "sources": sources,
            "companiesAvailable": max((int(item.get("companiesAvailable", 0)) for item in summaries), default=0),
            "companiesScanned": sum(int(item.get("companiesScanned", 0)) for item in summaries),
            "capHit": any(bool(item.get("capHit")) for item in summaries),
            "datasetStatus": dataset_status,
            "sourceStats": source_stats,
            "postingsKept": sum(int(item.get("postingsKept", 0)) for item in summaries),
            "postingsDroppedNoDate": sum(int(item.get("postingsDroppedNoDate", 0)) for item in summaries),
            "unreachableBoards": sum(int(item.get("unreachableBoards", 0)) for item in summaries),
            "offers": [offer for item in summaries for offer in item.get("offers", [])],
        }
        return ATSScanResult(candidates=candidates, summary=summary)

    @staticmethod
    def _candidate(offer: Any) -> JobCandidate | None:
        if not isinstance(offer, dict):
            return None
        title = str(offer.get("title") or "").strip()
        employer = str(offer.get("company") or "").strip()
        raw_url = str(offer.get("url") or "").strip()
        if not title or not employer or not raw_url:
            return None

        canonical_url = normalize_url(raw_url)
        description = str(offer.get("description") or "").strip()
        location = str(offer.get("location") or "").strip()
        source = str(offer.get("source") or "ats_unknown").strip()
        digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]
        content_hash = compute_content_hash(f"{employer} {title} {description[:500]}")
        return JobCandidate(
            id=f"ats_{digest}",
            source=source,
            canonical_url=canonical_url,
            employer=employer,
            title=title,
            location=normalize_location(location) if location else "",
            description=description,
            publication_date=str(offer.get("postedAt") or "") or None,
            status="DISCOVERED",
            content_hash=content_hash,
        )
