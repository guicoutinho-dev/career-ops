import sqlite3
import datetime
from pathlib import Path
from typing import List, Tuple
from src.database.connection import get_connection

MIGRATIONS_DIR = Path("migrations")

def apply_migrations(db_path: Path | str = "data/applications.db") -> List[str]:
    """Applies all pending SQL migrations in ascending order."""
    conn = get_connection(db_path)
    applied_now = []
    try:
        # Ensure migration table exists
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                migration_name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
        """)
        conn.commit()

        # Get already applied versions
        cursor = conn.cursor()
        cursor.execute("SELECT version FROM schema_migrations ORDER BY version ASC;")
        applied_versions = {row[0] for row in cursor.fetchall()}

        # Scan migrations folder
        if not MIGRATIONS_DIR.exists():
            return applied_now

        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for file in migration_files:
            prefix = file.name.split("_")[0]
            if not prefix.isdigit():
                continue
            version = int(prefix)
            if version not in applied_versions:
                sql_content = file.read_text(encoding="utf-8")
                conn.executescript(sql_content)
                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO schema_migrations (version, migration_name, applied_at) VALUES (?, ?, ?);",
                    (version, file.name, now_iso)
                )
                conn.commit()
                applied_now.append(file.name)
    finally:
        conn.close()
    return applied_now

if __name__ == "__main__":
    result = apply_migrations()
    print(f"Applied migrations: {result}")
