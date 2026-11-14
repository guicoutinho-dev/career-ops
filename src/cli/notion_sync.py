import sys
import argparse
from pathlib import Path
from src.integrations.notion.adapter import NotionAdapter

def load_env_file():
    """Ensures .env variables are loaded into os.environ if python-dotenv is not installed."""
    import os
    env_path = Path(".env")
    if not env_path.exists():
        env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip().strip('"').strip("'")

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    load_env_file()
    parser = argparse.ArgumentParser(description="Synchronize Australia Job Hunter applications to Notion.")
    parser.add_argument("--app-id", type=str, help="Sync specific application ID")
    parser.add_argument("--limit", type=int, default=40, help="Maximum candidates to sync (default: 40)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate sync without contacting Notion API")
    args = parser.parse_args()

    adapter = NotionAdapter(dry_run=args.dry_run)


    print("==================================================")
    print("      AUSTRALIA JOB HUNTER — NOTION SYNC         ")
    print("==================================================")
    if adapter.dry_run:
        print("Mode: DRY-RUN / OFFLINE (No Notion API token or --dry-run requested)")
    else:
        print("Mode: LIVE NOTION SYNCHRONIZATION")

    if args.app_id:
        print(f"Syncing single application: {args.app_id}...")
        success, msg = adapter.sync_application(args.app_id)
        status_tag = "[OK]" if success else "[FAIL]"
        print(f"{status_tag} {msg}")
    else:
        print(f"Syncing evaluated/qualified applications (limit: {args.limit})...")
        report = adapter.sync_all_applications(limit=args.limit)
        print(f"\nTotal Candidates Processed: {report.get('total_candidates', 0)}")
        print(f"Successfully Synced:        {report.get('synced', 0)}")
        print(f"Failed / Queued:            {report.get('failed', 0)}")
        failures = report.get("failures", [])
        for item in failures[:10]:
            print(f"  - {item['id']}: {item['error']}")
        if len(failures) > 10:
            print(f"  ... and {len(failures) - 10} more.")

    print("==================================================")

if __name__ == "__main__":
    main()
