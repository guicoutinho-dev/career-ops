from pathlib import Path
from typing import Iterable

from src.database.connection import get_connection
from src.models.job import JobCandidate
from src.services.deduplicator import DeduplicationService


def ingest_discovered_jobs(
    candidates: Iterable[JobCandidate], db_path: Path | str = "data/applications.db"
) -> dict[str, int]:
    """Persist only non-duplicate discoveries in one serialized SQLite transaction."""
    conn = get_connection(db_path)
    counts = {"inserted": 0, "duplicates": 0}
    try:
        conn.execute("BEGIN IMMEDIATE")
        deduplicator = DeduplicationService(conn)
        for candidate in candidates:
            duplicate, _reason = deduplicator.check_duplicate(candidate)
            if duplicate:
                counts["duplicates"] += 1
                continue
            conn.execute(
                """INSERT INTO jobs (
                    id, source, external_id, canonical_url, employer, title, location, description,
                    publication_date, discovered_at, status, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
                (
                    candidate.id,
                    candidate.source,
                    candidate.external_id,
                    candidate.canonical_url,
                    candidate.employer,
                    candidate.title,
                    candidate.location,
                    candidate.description,
                    candidate.publication_date,
                    candidate.discovered_at,
                    candidate.status,
                    candidate.content_hash,
                ),
            )
            counts["inserted"] += 1
        conn.commit()
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
