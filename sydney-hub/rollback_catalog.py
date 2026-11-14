"""Restore a verified pre-migration or pre-pilot SQLite snapshot."""

from pathlib import Path
import argparse

from hub.storage.db import restore_catalog_backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Restore a verified SQLite snapshot')
    parser.add_argument('--force', action='store_true', help='Preserve current database in a safety backup before restoring')
    parser.add_argument('--target-version', type=int, choices=(0, 1, 2), default=0,
                        help='Restore before the initial catalog (0), schema v2 upgrade (1), or employer pilot (2)')
    parser.add_argument('--database', type=Path, default=Path(__file__).resolve().parent / 'data' / 'hub.db')
    args = parser.parse_args()
    database = args.database
    preserved = restore_catalog_backup(database, force=args.force, target_version=args.target_version)
    print(f"Restored {database} to schema version {args.target_version}")
    if preserved:
        print(f"Newer writes were preserved in {preserved}")
