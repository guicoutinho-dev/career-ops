import re
import sqlite3
import datetime
import hashlib
from pathlib import Path
from typing import Dict, List, Any
from src.database.connection import get_connection
from src.services.normalizer import normalize_url, compute_content_hash

APPLICATIONS_MD_PATH = Path("data/applications.md")

def parse_applications_md(file_path: Path = APPLICATIONS_MD_PATH) -> List[Dict[str, str]]:
    """Parses data/applications.md into structured row dictionaries."""
    if not file_path.exists():
        return []

    lines = file_path.read_text(encoding="utf-8").splitlines()
    rows = []
    header_found = False

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        parts = [p.strip() for p in stripped.split("|")[1:-1]]
        if not parts:
            continue
        if parts[0] == "#" and parts[1] == "Date":
            header_found = True
            continue
        if header_found and all(set(p) <= {"-", " "} for p in parts):
            continue  # Separator line
        if header_found and len(parts) >= 9:
            row_dict = {
                "num": parts[0],
                "date": parts[1],
                "company": parts[2],
                "role": parts[3],
                "score": parts[4],
                "status": parts[5],
                "pdf": parts[6],
                "report": parts[7],
                "notes": parts[8],
            }
            rows.append(row_dict)
    return rows

def sync_applications_md_to_sqlite(
    db_path: Path | str = "data/applications.db",
    file_path: Path = APPLICATIONS_MD_PATH,
) -> int:
    """Import legacy tracker rows without overwriting SQLite-owned applications."""
    rows = parse_applications_md(file_path)
    if not rows:
        return 0

    conn = get_connection(db_path)
    synced_count = 0
    try:
        cursor = conn.cursor()
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        for r in rows:
            app_id = f"app_{r['num']}"
            job_id = f"job_{r['num']}"
            
            # Extract link if present in notes or fallback
            link_match = re.search(r"https?://[^\s|]+", r["notes"])
            canonical_url = normalize_url(link_match.group(0)) if link_match else f"urn:career-ops:job:{r['num']}"
            
            # Parse score
            try:
                score_val = float(r["score"])
            except ValueError:
                score_val = None

            # 1. Upsert into jobs
            content_hash = compute_content_hash(f"{r['company']} {r['role']}")
            cursor.execute("""
                INSERT INTO jobs (
                    id, source, canonical_url, employer, title, location, description,
                    publication_date, discovered_at, status, content_hash
                ) VALUES (?, 'career_ops_legacy', ?, ?, ?, 'Australia', ?, ?, ?, 'QUALIFIED', ?)
                ON CONFLICT(canonical_url) DO UPDATE SET
                    employer = excluded.employer,
                    title = excluded.title,
                    last_verified_at = ?;
            """, (
                job_id, canonical_url, r["company"], r["role"],
                f"Legacy imported role from applications.md (#{r['num']}). Notes: {r['notes']}",
                r["date"], now_iso, content_hash, now_iso
            ))

            # Retrieve actual job_id to satisfy foreign key integrity
            cursor.execute("SELECT id FROM jobs WHERE canonical_url = ?;", (canonical_url,))
            actual_row = cursor.fetchone()
            actual_job_id = actual_row[0] if actual_row else job_id

            # 2. Upsert into applications
            cursor.execute("""
                INSERT INTO applications (
                    id, job_id, profile_id, role_track, match_score, confidence,
                    status, created_at, updated_at, assigned_owner
                ) VALUES (?, ?, 'default_profile', 'sap', ?, 0.95, ?, ?, ?, 'Antigravity_Mestre')
                ON CONFLICT(id) DO NOTHING;
            """, (
                app_id, actual_job_id, score_val, r["status"],
                r["date"] + "T00:00:00Z", now_iso
            ))
            synced_count += 1

        conn.commit()
    finally:
        conn.close()

    return synced_count

PIPELINE_MD_PATH = Path("data/pipeline.md")

def sync_pipeline_md_to_sqlite(file_path: Path = PIPELINE_MD_PATH, db_path: Path | str = "data/applications.db", limit: int = 100) -> int:
    """Parses un-processed rows from data/pipeline.md and adds them as DISCOVERED jobs."""
    if not file_path.exists():
        return 0

    conn = get_connection(db_path)
    inserted_count = 0
    try:
        cursor = conn.cursor()
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        lines = file_path.read_text(encoding="utf-8").splitlines()

        for line in lines:
            line_str = line.strip()
            if not line_str.startswith("- [ ]"):
                continue

            parts = [p.strip() for p in line_str.replace("- [ ]", "").split("|")]
            if len(parts) < 3:
                continue

            raw_url = parts[0]
            company = parts[1]
            title = parts[2]
            location = parts[3] if len(parts) > 3 else "Australia"
            posted_date = None
            if len(parts) > 4 and "posted:" in parts[4]:
                posted_date = parts[4].replace("posted:", "").strip()

            canonical_url = normalize_url(raw_url)
            content_hash = compute_content_hash(f"{company} {title} {location}")
            job_id = f"seek_{content_hash[:12]}"

            cursor.execute("""
                INSERT OR IGNORE INTO jobs (
                    id, source, canonical_url, employer, title, location, description,
                    publication_date, discovered_at, status, content_hash
                ) VALUES (?, 'seek_australia', ?, ?, ?, ?, ?, ?, ?, 'DISCOVERED', ?);
            """, (
                job_id, canonical_url, company, title, location,
                f"Opportunity: {title} at {company} ({location}). Full details at {canonical_url}",
                posted_date, now_iso, content_hash
            ))

            if cursor.rowcount > 0:
                inserted_count += 1
                if inserted_count >= limit:
                    break

        conn.commit()
    finally:
        conn.close()

    return inserted_count

if __name__ == "__main__":
    count_apps = sync_applications_md_to_sqlite()
    count_pipe = sync_pipeline_md_to_sqlite()
    print(f"Synced {count_apps} applications and ingested {count_pipe} pipeline jobs into SQLite.")

