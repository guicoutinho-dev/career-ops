"""
CLI for creating and verifying database backups.
Usage:
    python -m src.cli.backup --backup
    python -m src.cli.backup --list
    python -m src.cli.backup --restore backups/applications_backup_YYYYMMDD_HHMMSS.db
"""

import argparse
import sys
from pathlib import Path
from src.services.backup import BackupService


def main() -> None:
    parser = argparse.ArgumentParser(description="Career-Ops SQLite Database Backup CLI")
    parser.add_argument("--backup", action="store_true", help="Create a new atomic backup with integrity check")
    parser.add_argument("--list", action="store_true", help="List all available database backups")
    parser.add_argument("--restore", type=str, help="Restore database from a specific backup file")
    parser.add_argument("--prune", type=int, default=14, help="Retention days to keep backups (default: 14)")

    args = parser.parse_args()
    service = BackupService()

    if args.backup:
        print("[BACKUP] Starting SQLite online backup...")
        dest = service.create_backup()
        print(f"[SUCCESS] Backup successfully created at: {dest}")
        pruned = service.prune_old_backups(retention_days=args.prune)
        if pruned > 0:
            print(f"[PRUNE] Removed {pruned} old backup(s) exceeding {args.prune} days.")

    elif args.list:
        backups = service.list_backups()
        if not backups:
            print("[INFO] No backups found in backups/ directory.")
            return
        print(f"Found {len(backups)} backup(s):")
        for b in backups:
            size_kb = b.stat().st_size / 1024
            print(f" - {b.name} ({size_kb:.1f} KB)")

    elif args.restore:
        backup_path = Path(args.restore)
        if not backup_path.exists():
            print(f"[ERROR] Backup file not found: {backup_path}")
            sys.exit(1)
        print(f"[RESTORE] Restoring database from {backup_path}...")
        service.restore_backup(backup_path)
        print("[SUCCESS] Database restored successfully and passed integrity verification.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
