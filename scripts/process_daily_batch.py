import os
import sys
import datetime
import logging
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.database.connection import get_connection
from src.integrations.career_ops.sync import sync_pipeline_md_to_sqlite
from src.services.job_classifier import classify_job, CATEGORY_SAP, CATEGORY_88D, CATEGORY_HOSP, CATEGORY_OFFICE
from src.services.match_analyst import MatchAnalyst
from src.services.event_logger import EventLogger
from src.integrations.notion.adapter import NotionAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("process_daily_batch")

def run_daily_processing():
    db_path = PROJECT_ROOT / "data" / "applications.db"
    
    # 1. Ingest newly scanned jobs from pipeline.md into SQLite
    ingested = sync_pipeline_md_to_sqlite(db_path=db_path, limit=500)
    logger.info(f"Ingested {ingested} pending jobs from pipeline.md into SQLite.")

    # 2. Evaluate all DISCOVERED jobs
    conn = get_connection(db_path)
    analyst = MatchAnalyst()
    event_logger = EventLogger(conn)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, title, description, location, employer, publication_date
        FROM jobs
        WHERE status = 'DISCOVERED';
    """)
    rows = cursor.fetchall()
    logger.info(f"Evaluating {len(rows)} DISCOVERED jobs in SQLite...")

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    sap_qualified = []
    sap_disqualified_clearance = []
    whv_qualified = []
    whv_disqualified = []

    for r in rows:
        job_id = r["id"]
        title = r["title"] or ""
        desc = r["description"] or ""
        loc = r["location"] or ""
        employer = r["employer"] or ""
        pub_date = r["publication_date"]

        category, visa_fit = classify_job(title, desc, loc)
        role_track = "sap" if category == CATEGORY_SAP else "operational"

        res = analyst.evaluate_job(
            job_id=job_id,
            title=title,
            description=desc,
            location=loc,
            employer=employer,
            role_track=role_track,
            publication_date=pub_date,
            category=category
        )

        new_status = "QUALIFIED" if res.recommendation == "AUTO_QUALIFY" else (
            "DISQUALIFIED" if res.recommendation == "AUTO_DISQUALIFY" else "NEEDS_REVIEW"
        )

        # Track metrics
        if role_track == "sap":
            if res.hard_blockers:
                sap_disqualified_clearance.append((employer, title, res.hard_blockers))
            elif new_status == "QUALIFIED":
                sap_qualified.append((employer, title, loc, res.scaled_score_5))
        else:
            if res.hard_blockers:
                whv_disqualified.append((employer, title, res.hard_blockers))
            elif new_status == "QUALIFIED":
                whv_qualified.append((employer, title, loc, category, visa_fit, res.scaled_score_5))

        # Update job status
        cursor.execute("UPDATE jobs SET status = ? WHERE id = ?;", (new_status, job_id))

        # Upsert into applications table with category and visa_fit
        app_id = f"app_{job_id}"
        cursor.execute("""
            INSERT INTO applications (
                id, job_id, profile_id, role_track, match_score, confidence, status,
                category, visa_fit, created_at, updated_at
            ) VALUES (?, ?, 'default_profile', ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                role_track = excluded.role_track,
                match_score = excluded.match_score,
                confidence = excluded.confidence,
                status = excluded.status,
                category = excluded.category,
                visa_fit = excluded.visa_fit,
                updated_at = excluded.updated_at;
        """, (
            app_id, job_id, role_track, res.scaled_score_5, res.confidence, new_status,
            category, visa_fit, now_iso, now_iso
        ))

        # Log audit event
        event_logger.log_event(
            entity_type="job",
            entity_id=job_id,
            event_type="JOB_EVALUATED",
            actor="system:MatchAnalyst",
            correlation_id=f"daily_{job_id}",
            payload={
                "recommendation": res.recommendation,
                "score": res.scaled_score_5,
                "category": category,
                "visa_fit": visa_fit,
                "hard_blockers": res.hard_blockers
            }
        )

    conn.commit()
    conn.close()

    print("\n==================================================")
    print("        AVALIAÇÃO E FILTRAGEM CONCLUÍDA           ")
    print("==================================================")
    print(f"Total avaliado               : {len(rows)}")
    print(f"Trilha SAP Qualificadas      : {len(sap_qualified)}")
    print(f"Trilha SAP Bloqueadas (Seg.) : {len(sap_disqualified_clearance)}")
    print(f"Trilha 88 Dias Qualificadas  : {len(whv_qualified)}")
    print(f"Trilha 88 Dias Bloqueadas    : {len(whv_disqualified)}")

    # 3. Sincronização com o Notion
    print("\nSincronizando novas qualificações com o Notion...")
    try:
        adapter = NotionAdapter()
        report = adapter.sync_all_applications(limit=60)
        print(f"Notion - Processados : {report.get('total_candidates', 0)}")
        print(f"Notion - Sincronizados: {report.get('synced', 0)}")
        print(f"Notion - Falhas/Fila  : {report.get('failed', 0)}")
    except Exception as e:
        print(f"Erro ao sincronizar com Notion: {e}")

    return {
        "sap_qualified": sap_qualified,
        "sap_blocked": sap_disqualified_clearance,
        "whv_qualified": whv_qualified,
        "whv_blocked": whv_disqualified
    }

if __name__ == "__main__":
    run_daily_processing()
