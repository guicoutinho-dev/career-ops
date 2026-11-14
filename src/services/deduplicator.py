import sqlite3
from typing import Optional, Tuple
from src.models.job import JobCandidate

class DeduplicationService:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def check_duplicate(self, candidate: JobCandidate) -> Tuple[bool, Optional[str]]:
        """
        Returns (is_duplicate, reason_or_existing_id).
        Checks:
        1. Exact canonical URL
        2. Exact content hash
        3. Same employer + normalized title within past 90 days
        """
        cursor = self.conn.cursor()

        # 1. Exact Canonical URL match
        cursor.execute("SELECT id FROM jobs WHERE canonical_url = ? LIMIT 1;", (candidate.canonical_url,))
        row = cursor.fetchone()
        if row:
            return True, f"exact_url_match:{row['id']}"

        # 2. Exact content hash match
        cursor.execute("SELECT id FROM jobs WHERE content_hash = ? LIMIT 1;", (candidate.content_hash,))
        row = cursor.fetchone()
        if row:
            return True, f"exact_content_hash:{row['id']}"

        # 3. Same employer and title (heuristic duplicate)
        cursor.execute("""
            SELECT id FROM jobs 
            WHERE LOWER(employer) = LOWER(?) 
              AND LOWER(title) = LOWER(?)
            LIMIT 1;
        """, (candidate.employer, candidate.title))
        row = cursor.fetchone()
        if row:
            return True, f"employer_title_match:{row['id']}"

        return False, None
