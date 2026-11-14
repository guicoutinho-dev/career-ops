"""
Online SQLite backup and recovery service using SQLite's backup API.
Provides transactional snapshots, PRAGMA integrity verification, and pruning.
"""

import os
import sqlite3
import datetime
from pathlib import Path
from typing import Tuple, List

DEFAULT_DB_PATH = Path("data/applications.db")
BACKUP_DIR = Path("data/backups")

def create_backup(db_path: Path | str = DEFAULT_DB_PATH, backup_dir: Path | str = BACKUP_DIR) -> Tuple[bool, str]:
    """Creates a point-in-time snapshot backup using SQLite's online backup API."""
    src_path = Path(db_path)
    if not src_path.exists():
        return False, f"Source database '{src_path}' does not exist."

    dst_dir = Path(backup_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    dst_path = dst_dir / f"applications_backup_{timestamp}.db"

    try:
        src_conn = sqlite3.connect(str(src_path))
        dst_conn = sqlite3.connect(str(dst_path))

        with dst_conn:
            src_conn.backup(dst_conn)

        dst_conn.close()
        src_conn.close()

        check_conn = sqlite3.connect(str(dst_path))
        cursor = check_conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        integrity_result = cursor.fetchone()[0]
        check_conn.close()

        if integrity_result != "ok":
            return False, f"Backup created but integrity check failed: {integrity_result}"

        return True, str(dst_path)
    except Exception as e:
        return False, f"Backup error: {e}"

def restore_backup(backup_path: Path | str, target_db_path: Path | str = DEFAULT_DB_PATH) -> Tuple[bool, str]:
    """Restores database from a verified backup snapshot."""
    src_path = Path(backup_path)
    if not src_path.exists():
        return False, f"Backup file '{src_path}' not found."

    dst_path = Path(target_db_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        # Pre-check integrity of backup before restoring
        check_conn = sqlite3.connect(str(src_path))
        cursor = check_conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        if cursor.fetchone()[0] != "ok":
            check_conn.close()
            return False, "Backup file failed integrity verification."
        check_conn.close()

        src_conn = sqlite3.connect(str(src_path))
        dst_conn = sqlite3.connect(str(dst_path))

        with dst_conn:
            src_conn.backup(dst_conn)

        dst_conn.close()
        src_conn.close()
        return True, f"Restored {dst_path} from {src_path}"
    except Exception as e:
        return False, f"Restore error: {e}"

def list_backups(backup_dir: Path | str = BACKUP_DIR) -> List[Path]:
    """List available backups ordered from newest to oldest."""
    b_dir = Path(backup_dir)
    if not b_dir.exists():
        return []
    return sorted(list(b_dir.glob("applications_backup_*.db")), key=os.path.getmtime, reverse=True)

def prune_backups(backup_dir: Path | str = BACKUP_DIR, retention_days: int = 14) -> int:
    """Removes backup files older than retention_days."""
    b_dir = Path(backup_dir)
    if not b_dir.exists():
        return 0
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(days=retention_days)
    pruned = 0
    for file in b_dir.glob("applications_backup_*.db"):
        mtime = datetime.datetime.fromtimestamp(file.stat().st_mtime, tz=datetime.timezone.utc)
        if mtime < cutoff:
            file.unlink()
            pruned += 1
    return pruned

class BackupService:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH, backup_dir: Path | str = BACKUP_DIR):
        self.db_path = Path(db_path)
        self.backup_dir = Path(backup_dir)

    def create_backup(self) -> str:
        ok, res = create_backup(self.db_path, self.backup_dir)
        if not ok:
            raise RuntimeError(res)
        return res

    def restore_backup(self, backup_path: Path | str) -> str:
        ok, res = restore_backup(backup_path, self.db_path)
        if not ok:
            raise RuntimeError(res)
        return res

    def list_backups(self) -> List[Path]:
        return list_backups(self.backup_dir)

    def prune_old_backups(self, retention_days: int = 14) -> int:
        return prune_backups(self.backup_dir, retention_days)
