"""Restore a verified pre-migration or pre-pilot SQLite snapshot."""

from pathlib import Path
import argparse

from hub.storage.db import restore_catalog_backup, restore_published_run


if __name__ == "__main__":
    default_root = Path(__file__).resolve().parent / "data" / "jobs_isolated"
    parser = argparse.ArgumentParser(description="Restore a verified SQLite snapshot")
    parser.add_argument("--force", action="store_true", help="Preserve current database in a safety backup before restoring")
    parser.add_argument(
        "--target-version",
        type=int,
        choices=(0, 1, 2, 3, 4, 5, 6),
        default=0,
        help="Restore before initial catalog (0), schema v2 (1), v3 (2), v4 (3), Track 1 pilot (4), published v5 (5), or lifecycle v6 (6)",
    )
    parser.add_argument("--pre-run", action="store_true", help="Restore the pre-run snapshot of an isolated Jobs DB")
    parser.add_argument("--isolated-root", type=Path, default=default_root, help="Approved isolated Jobs DB directory")
    parser.add_argument("--database", type=Path, default=Path(__file__).resolve().parent / "data" / "hub.db")
    args = parser.parse_args()
    database = args.database
    if args.pre_run:
        preserved = restore_published_run(database, isolated_root=args.isolated_root, force=args.force)
        print(f"Restored pre-run isolated Jobs snapshot for {database}")
    else:
        preserved = restore_catalog_backup(database, force=args.force, target_version=args.target_version)
        print(f"Restored {database} to schema version {args.target_version}")
    if preserved:
        print(f"Newer writes were preserved in {preserved}")
