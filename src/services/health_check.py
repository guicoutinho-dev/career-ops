import os
import sqlite3
import shutil
from pathlib import Path
from typing import Dict, Any
from src.database.connection import get_connection

class HealthCheckService:
    """Performs comprehensive system, database, and operational health checks."""

    def __init__(self, db_path: Path | str = "data/applications.db"):
        self.db_path = Path(db_path)

    def run_health_check(self) -> Dict[str, Any]:
        report = {
            "status": "HEALTHY",
            "checks": {},
            "issues": []
        }

        # 1. Database Connectivity & File Existence
        if not self.db_path.exists():
            report["status"] = "UNHEALTHY"
            report["checks"]["database_exists"] = False
            report["issues"].append(f"Database file '{self.db_path}' not found.")
            return report
        report["checks"]["database_exists"] = True

        # 2. SQLite PRAGMA Integrity Check
        try:
            conn = get_connection(self.db_path)
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            integrity_row = cursor.fetchone()
            integrity = integrity_row[0] if integrity_row else "unknown"
            conn.close()

            if integrity == "ok":
                report["checks"]["sqlite_integrity"] = "ok"
            else:
                report["status"] = "DEGRADED"
                report["checks"]["sqlite_integrity"] = integrity
                report["issues"].append(f"SQLite integrity check failed: {integrity}")
        except Exception as e:
            report["status"] = "UNHEALTHY"
            report["checks"]["sqlite_integrity"] = f"error: {e}"
            report["issues"].append(f"Cannot connect to SQLite: {e}")
            return report

        # 3. Tables & Record Counts
        try:
            conn = get_connection(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM jobs;")
            jobs_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM applications;")
            apps_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM approvals WHERE decision = 'APPROVED';")
            approved_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM tasks WHERE status = 'FAILED';")
            failed_tasks = cursor.fetchone()[0]
            conn.close()

            report["checks"]["jobs_count"] = jobs_count
            report["checks"]["applications_count"] = apps_count
            report["checks"]["approved_applications"] = approved_count
            report["checks"]["failed_tasks"] = failed_tasks

            if failed_tasks > 5:
                report["status"] = "DEGRADED"
                report["issues"].append(f"High number of failed tasks detected: {failed_tasks}")
        except Exception as e:
            report["checks"]["record_count_error"] = str(e)

        # 4. Storage & Disk Space
        try:
            usage = shutil.disk_usage(Path.cwd())
            free_gb = round(usage.free / (1024 ** 3), 2)
            report["checks"]["disk_free_gb"] = free_gb
            if free_gb < 1.0:
                report["status"] = "DEGRADED"
                report["issues"].append(f"Low disk space: {free_gb} GB remaining.")
        except Exception:
            pass

        # 5. Master CV Presence
        cv_path = Path("cv.md")
        report["checks"]["master_cv_present"] = cv_path.exists()
        if not cv_path.exists():
            report["status"] = "DEGRADED"
            report["issues"].append("Master CV ('cv.md') not found.")

        return report
