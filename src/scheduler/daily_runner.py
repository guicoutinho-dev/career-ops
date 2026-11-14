import datetime
import yaml
from pathlib import Path
from typing import Dict, Any

from src.database.connection import get_connection
from src.database.migrations import apply_migrations
from src.services.task_queue import TaskQueue
from src.services.batch_evaluator import evaluate_pending_jobs
from src.services.backup import create_backup
from src.services.health_check import HealthCheckService
from src.services.event_logger import EventLogger
from src.integrations.career_ops.sync import sync_pipeline_md_to_sqlite, sync_applications_md_to_sqlite
from src.integrations.ats_full.adapter import ATSAggregatorAdapter
from src.services.discovery import ingest_discovered_jobs
from src.services.search_profiles import load_search_profiles

class DailyRunner:
    """Orchestrates daily morning pipeline: recovery, discovery, evaluation, reporting, and backup."""

    def __init__(
        self,
        db_path: Path | str = "data/applications.db",
        discovery_adapter=None,
        report_dir: Path | str | None = None,
        project_root: Path | str | None = None,
    ):
        self.db_path = Path(db_path)
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[2]
        self.report_dir = Path(report_dir) if report_dir else Path("reports")
        apply_migrations(self.db_path)
        self.health_service = HealthCheckService(self.db_path)
        self.discovery_adapter = discovery_adapter or ATSAggregatorAdapter(project_root=self.project_root)

    def _run_discovery(self) -> Dict[str, Any]:
        sources_path = self.project_root / "config" / "sources.yaml"
        source_config = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
        configured = source_config.get("sources", {})
        statuses: Dict[str, Any] = {}
        discovered = {"inserted": 0, "duplicates": 0}
        attempted = False

        ats_config = configured.get("ats_aggregators", {})
        if ats_config.get("enabled", False):
            try:
                profiles = load_search_profiles(self.project_root / "config" / "search_profiles.yaml")
                providers = ats_config.get("providers", [])
                scan = self.discovery_adapter.scan_profiles(
                    profiles=profiles,
                    ats=providers,
                    since_days=int(ats_config.get("since_days", 7)),
                    limit_per_ats=int(ats_config.get("limit_per_ats", 150)),
                )
                attempted = True
                discovered = ingest_discovered_jobs(scan.candidates, db_path=self.db_path)
                stats = scan.summary
                dataset_status = stats.get("datasetStatus", {})
                partial = (
                    bool(stats.get("capHit"))
                    or bool(stats.get("unreachableBoards"))
                    or any(value != "ok" for value in dataset_status.values())
                )
                statuses["ats_aggregators"] = {
                    "status": "partial" if partial else "completed",
                    "providers": providers,
                    "dataset_status": dataset_status,
                    "companies_available": stats.get("companiesAvailable", 0),
                    "companies_scanned": stats.get("companiesScanned", 0),
                    "cap_hit": bool(stats.get("capHit")),
                    "matches": stats.get("postingsKept", len(scan.candidates)),
                    "unreachable_boards": stats.get("unreachableBoards", 0),
                    **discovered,
                }
                source_stats = stats.get("sourceStats", {})
                for provider in providers:
                    provider_stats = source_stats.get(provider, {})
                    statuses[f"ats:{provider}"] = {
                        "status": dataset_status.get(provider, "unknown"),
                        "companies_available": provider_stats.get("companiesAvailable", 0),
                        "companies_scanned": provider_stats.get("companiesScanned", 0),
                        "unreachable_boards": provider_stats.get("unreachableBoards", 0),
                        "matches": provider_stats.get("matches", 0),
                        "postings_dropped_no_date": provider_stats.get("postingsDroppedNoDate", 0),
                    }
            except Exception as exc:
                attempted = True
                statuses["ats_aggregators"] = {"status": "failed", "message": str(exc)}
        else:
            statuses["ats_aggregators"] = {"status": "disabled"}

        seek_config = configured.get("seek_native", {})
        if seek_config.get("enabled", False):
            statuses["seek_native"] = {
                "status": "blocked",
                "reason": "SEEK search API requires approved partner access; no scraping or API request was made.",
            }
        else:
            statuses["seek_native"] = {"status": "disabled"}

        jobspy_config = configured.get("jobspy", {})
        if jobspy_config.get("enabled", False):
            statuses["jobspy"] = {
                "status": "blocked",
                "reason": "Configured JobSpy sources are blocked pending provider authorization and policy clearance; no automated requests were made.",
            }
        else:
            statuses["jobspy"] = {"status": "disabled"}

        return {"sources": statuses, "discovered": discovered, "attempted": attempted}

    @staticmethod
    def _format_source_status(name: str, details: Dict[str, Any]) -> str:
        line = f"- **{name}:** {details.get('status', 'unknown')}"
        counts = []
        if "companies_scanned" in details:
            counts.append(
                f"{details.get('companies_scanned', 0)}/{details.get('companies_available', 0)} companies"
            )
        for key, label in (
            ("matches", "matches"),
            ("inserted", "inserted"),
            ("duplicates", "duplicates"),
            ("unreachable_boards", "unreachable"),
            ("postings_dropped_no_date", "undated skipped"),
        ):
            if key in details:
                counts.append(f"{details[key]} {label}")
        if details.get("cap_hit"):
            counts.append("company limit reached")
        if details.get("dataset_status"):
            counts.append(f"datasets {details['dataset_status']}")
        if counts:
            line += " — " + ", ".join(counts)
        reason = details.get("reason") or details.get("message")
        if reason:
            line += f" — {reason}"
        return line

    def run_daily_cycle(self, eval_limit: int = 50) -> Dict[str, Any]:
        start_time = datetime.datetime.now(datetime.timezone.utc)
        today_str = start_time.strftime("%Y-%m-%d")
        summary: Dict[str, Any] = {
            "date": today_str,
            "started_at": start_time.isoformat(),
            "sources_attempted": ["Pipeline Feed", "Career-ops Flat Sync"],
            "discovery_sources": {},
            "new_jobs_from_ats": 0,
            "recovered_stale_tasks": 0,
            "new_jobs_ingested": 0,
            "evaluated_jobs": 0,
            "qualified_count": 0,
            "disqualified_count": 0,
            "pending_approvals": 0,
            "backup_path": None,
            "health_status": "UNKNOWN"
        }

        # 1. Recover stale tasks stuck in RUNNING
        conn = get_connection(self.db_path)
        try:
            tq = TaskQueue(conn)
            recovered = tq.recover_stale_tasks(timeout_minutes=15)
            summary["recovered_stale_tasks"] = recovered
        finally:
            conn.close()

        # 2. Search public ATS boards; SEEK/JobSpy remain blocked pending authorization.
        discovery = self._run_discovery()
        summary["discovery_sources"] = discovery["sources"]
        summary["new_jobs_from_ats"] = discovery["discovered"]["inserted"]
        if discovery["attempted"]:
            summary["sources_attempted"].append("Public ATS boards (Greenhouse, Lever, Ashby, Workday)")

        # 3. Ingest un-processed items from pipeline.md
        ingested = sync_pipeline_md_to_sqlite(db_path=self.db_path, limit=200)
        summary["new_jobs_ingested"] = ingested

        # 4. Ingest/sync any tracker updates
        sync_applications_md_to_sqlite(db_path=self.db_path)

        # 5. Run MatchAnalyst evaluations on pending DISCOVERED jobs
        evaluations = evaluate_pending_jobs(limit=eval_limit, db_path=self.db_path)
        summary["evaluated_jobs"] = len(evaluations)
        for e in evaluations:
            if e.recommendation == "AUTO_QUALIFY":
                summary["qualified_count"] += 1
            elif e.recommendation == "AUTO_DISQUALIFY":
                summary["disqualified_count"] += 1

        # 5. Query active applications by status
        conn = get_connection(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM applications WHERE status = 'QUALIFIED';")
            summary["total_qualified_active"] = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM applications WHERE status = 'Evaluated';")
            summary["total_evaluated_active"] = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM applications WHERE status = 'APPROVED';")
            summary["total_approved_active"] = cursor.fetchone()[0]
        finally:
            conn.close()

        # 6. Create Point-in-Time Database Backup Snapshot
        backup_success, backup_path = create_backup(self.db_path)
        summary["backup_path"] = backup_path if backup_success else f"FAILED: {backup_path}"

        # 7. Health Check
        health = self.health_service.run_health_check()
        summary["health_status"] = health["status"]

        # 8. Generate Transparent Daily Report Markdown
        report_dir = self.report_dir
        report_dir.mkdir(parents=True, exist_ok=True)
        report_file = report_dir / f"daily-{today_str}.md"
        source_report = "\n".join(
            self._format_source_status(name, details)
            for name, details in summary["discovery_sources"].items()
        ) or "- No discovery sources were configured."

        report_md = f"""# Daily Run Summary Report — {today_str}

**Run Timestamp:** `{summary['started_at']}`  
**System Health:** `{summary['health_status']}`  
**Database Backup:** `{summary['backup_path']}`  

---

## 1. Operational Execution Summary
- **Sources Attempted:** {', '.join(summary['sources_attempted'])}
- **Stale Tasks Recovered:** {summary['recovered_stale_tasks']}
- **New Jobs from Public ATS Scan:** {summary['new_jobs_from_ats']}
- **New Jobs Ingested to Database:** {summary['new_jobs_ingested']}
- **Jobs Evaluated This Run:** {summary['evaluated_jobs']}
  - **Auto-Qualified (Score >= 75%):** {summary['qualified_count']}
  - **Auto-Disqualified (Blockers/Mismatch):** {summary['disqualified_count']}

## 2. Discovery Source Status
{source_report}

## 3. Active Pipeline Inventory (SQLite Autoritativo)
- **Awaiting Tailoring / Qualified:** {summary['total_qualified_active']}
- **Drafts Ready for Human Review (`Evaluated`):** {summary['total_evaluated_active']}
- **Approved by Operator (`APPROVED`):** {summary['total_approved_active']}

## 4. Human Gate & Approvals Pending
Os rascunhos gerados continuam isolados em `output/drafts/` e `applications/`. Nenhuma submissão automática é realizada sem autorização explícita do operador humano.

> Execute `python -m src.cli.status` para verificar o painel em tempo real.
"""
        report_file.write_text(report_md, encoding="utf-8")
        summary["report_path"] = str(report_file)

        # Log daily event
        conn = get_connection(self.db_path)
        try:
            logger = EventLogger(conn)
            logger.log_event(
                entity_type="system",
                entity_id=f"daily_{today_str}",
                event_type="DAILY_RUN_COMPLETED",
                actor="scheduler:DailyRunner",
                correlation_id=f"run_{today_str}",
                payload=summary
            )
        finally:
            conn.close()

        return summary
