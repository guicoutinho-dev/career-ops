import sqlite3
import datetime
from typing import List, Dict, Any
from src.database.connection import get_connection
from src.services.match_analyst import MatchAnalyst, MatchEvaluation
from src.services.event_logger import EventLogger
from src.services.job_classifier import CATEGORY_SAP, classify_job

def evaluate_pending_jobs(limit: int = 20, db_path=None) -> List[MatchEvaluation]:
    """Evaluates unassessed jobs from the SQLite database."""
    conn = get_connection(db_path) if db_path else get_connection()
    analyst = MatchAnalyst()
    logger = EventLogger(conn)
    evaluations = []

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, title, description, location, employer, publication_date
            FROM jobs
            WHERE status = 'DISCOVERED'
            LIMIT ?;
        """, (limit,))
        rows = cursor.fetchall()

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        for r in rows:
            category, visa_fit = classify_job(
                r["title"], r["description"], r["location"], role_track="operational"
            )
            role_track = "sap" if category == CATEGORY_SAP else "operational"
            res = analyst.evaluate_job(
                job_id=r["id"],
                title=r["title"],
                description=r["description"],
                location=r["location"],
                employer=r["employer"],
                role_track=role_track,
                publication_date=r["publication_date"],
                category=category,
            )
            if not (r["description"] or "").strip():
                res = res.model_copy(update={
                    "recommendation": "NEEDS_REVIEW",
                    "confidence": min(res.confidence, 0.3),
                    "gaps": [*res.gaps, "Full job description unavailable; human review required"],
                })
            evaluations.append(res)

            new_status = "QUALIFIED" if res.recommendation == "AUTO_QUALIFY" else (
                "DISQUALIFIED" if res.recommendation == "AUTO_DISQUALIFY" else "NEEDS_REVIEW"
            )

            # Update job status
            cursor.execute("UPDATE jobs SET status = ? WHERE id = ?;", (new_status, res.job_id))

            # Upsert into applications table
            app_id = f"app_{res.job_id}"
            cursor.execute("""
                INSERT INTO applications (
                    id, job_id, profile_id, role_track, match_score, confidence, status, created_at, updated_at,
                    category, visa_fit
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
                app_id, res.job_id, role_track, res.scaled_score_5, res.confidence,
                new_status, now_iso, now_iso, category, visa_fit,
            ))

            # Log audit event
            logger.log_event(
                entity_type="job",
                entity_id=res.job_id,
                event_type="JOB_EVALUATED",
                actor="system:MatchAnalyst",
                correlation_id=f"eval_{res.job_id}",
                payload={
                    "total_score": res.total_score,
                    "scaled_score_5": res.scaled_score_5,
                    "recommendation": res.recommendation,
                    "hard_blockers": res.hard_blockers,
                    "strengths": res.strengths[:3]
                }
            )

        conn.commit()
    finally:
        conn.close()

    return evaluations


# Statuses produced automatically by the scorer. Anything else (Evaluated, ready_for_review,
# APPROVED, APPLIED, ...) reflects human or agent decisions and is never overwritten.
AUTO_STATUSES = ("DISCOVERED", "QUALIFIED", "DISQUALIFIED", "NEEDS_REVIEW")


def reevaluate_by_category(db_path=None) -> Dict[str, int]:
    """
    Re-scores every automatically-scored application using its stored category so that
    category-specific skill catalogs apply. Returns counts of status transitions.
    """
    conn = get_connection(db_path) if db_path else get_connection()
    analyst = MatchAnalyst()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    changes = {"requalified": 0, "disqualified": 0, "unchanged": 0}
    try:
        placeholders = ",".join("?" * len(AUTO_STATUSES))
        rows = conn.execute(f"""
            SELECT a.id, a.status, a.role_track, a.category,
                   j.id AS job_id, j.title, j.description, j.location, j.employer, j.publication_date
            FROM applications a JOIN jobs j ON a.job_id = j.id
            WHERE a.status IN ({placeholders}) AND a.category IS NOT NULL;
        """, AUTO_STATUSES).fetchall()

        for r in rows:
            res = analyst.evaluate_job(
                job_id=r["job_id"], title=r["title"], description=r["description"],
                location=r["location"], employer=r["employer"], role_track=r["role_track"],
                publication_date=r["publication_date"], category=r["category"],
            )
            new_status = {"AUTO_QUALIFY": "QUALIFIED", "AUTO_DISQUALIFY": "DISQUALIFIED"}.get(
                res.recommendation, "NEEDS_REVIEW")
            if new_status == r["status"]:
                changes["unchanged"] += 1
            elif new_status == "QUALIFIED":
                changes["requalified"] += 1
            else:
                changes["disqualified"] += 1
            conn.execute(
                "UPDATE applications SET match_score = ?, confidence = ?, status = ?, updated_at = ? WHERE id = ?;",
                (res.scaled_score_5, res.confidence, new_status, now_iso, r["id"]),
            )
        conn.commit()
    finally:
        conn.close()
    return changes


if __name__ == "__main__":
    results = evaluate_pending_jobs()
    print(f"Evaluated {len(results)} jobs.")
    for r in results:
        print(f"[{r.recommendation}] Job: {r.job_id} | Score: {r.total_score}% ({r.scaled_score_5}/5) | Blockers: {r.hard_blockers}")
