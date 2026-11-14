import os
import sys
import logging
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.database.connection import get_connection
from src.integrations.jobspy.adapter import JobSpyAdapter
from src.services.deduplicator import DeduplicationService
from src.services.normalizer import normalize_url, compute_content_hash

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("jobspy_scan")

def run_jobspy_scan(hours_old: int = 48, results_per_query: int = 10):
    db_path = PROJECT_ROOT / "data" / "applications.db"
    pipeline_path = PROJECT_ROOT / "data" / "pipeline.md"
    scan_history_path = PROJECT_ROOT / "data" / "scan-history.tsv"

    conn = get_connection(db_path)
    dedup = DeduplicationService(conn)
    adapter = JobSpyAdapter(country="australia", rate_limit_delay=1.5)

    search_queries = [
        # Trilha 1: SAP Ecosystem
        ("SAP ABAP", "Australia"),
        ("SAP BTP", "Australia"),
        ("SAP Fiori", "Australia"),
        ("SAP Integration", "Australia"),
        # Trilha 2: 88 Dias WHV 462
        ("Farm Hand", "Australia"),
        ("Solar Farm", "Australia"),
        ("Fruit Picking", "Australia"),
        ("Regional Hospitality", "Australia"),
    ]

    total_scraped = 0
    total_added = 0
    total_duplicates = 0
    new_candidates = []

    for term, loc in search_queries:
        logger.info(f"Scanning JobSpy for '{term}' in {loc} (hours_old={hours_old})...")
        try:
            candidates = adapter.search_jobs(
                search_term=term,
                location=loc,
                results_wanted=results_per_query,
                sites=["indeed", "linkedin"],
                hours_old=hours_old
            )
            total_scraped += len(candidates)

            for cand in candidates:
                is_dupe, reason = dedup.check_duplicate(cand)
                if is_dupe:
                    total_duplicates += 1
                    continue

                # Insert into SQLite jobs
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR IGNORE INTO jobs (
                        id, source, canonical_url, employer, title, location, description,
                        publication_date, discovered_at, status, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 'DISCOVERED', ?);
                """, (
                    cand.id, cand.source, cand.canonical_url, cand.employer, cand.title,
                    cand.location, cand.description, cand.publication_date, cand.content_hash
                ))
                if cursor.rowcount > 0:
                    conn.commit()
                    total_added += 1
                    new_candidates.append(cand)

                    # Append to pipeline.md
                    with open(pipeline_path, "a", encoding="utf-8") as f:
                        f.write(f"- [ ] {cand.canonical_url} | {cand.employer} | {cand.title} | {cand.location} | posted: {cand.publication_date or 'recent'}\n")

                    # Append to scan-history.tsv
                    with open(scan_history_path, "a", encoding="utf-8") as f:
                        f.write(f"{cand.canonical_url}\t{cand.employer}\t{cand.title}\trecent\n")

        except Exception as e:
            logger.error(f"Error scanning term '{term}': {e}")

    conn.close()
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("JobSpy Australia Scan Complete")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"Total jobs scraped    : {total_scraped}")
    print(f"Duplicates skipped    : {total_duplicates}")
    print(f"New jobs added        : {total_added}")
    for c in new_candidates[:10]:
        print(f"  + {c.employer} | {c.title} | {c.location}")
    if len(new_candidates) > 10:
        print(f"  ... and {len(new_candidates) - 10} more.")
    return total_added

if __name__ == "__main__":
    run_jobspy_scan()
