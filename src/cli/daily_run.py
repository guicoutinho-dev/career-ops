import sys
import argparse
from src.scheduler.daily_runner import DailyRunner

def main():
    parser = argparse.ArgumentParser(description="Trigger Australia Job Hunter Daily Scheduled Pipeline Run.")
    parser.add_argument("--eval-limit", type=int, default=50, help="Max pending jobs to evaluate in this run")
    args = parser.parse_args()

    print("==================================================")
    print("      AUSTRALIA JOB HUNTER — DAILY RUNNER         ")
    print("==================================================")
    print("Executing morning cycle...")

    runner = DailyRunner()
    summary = runner.run_daily_cycle(eval_limit=args.eval_limit)

    print("\n[OK] Daily cycle completed!")
    print(f"  - System Health        : {summary['health_status']}")
    print(f"  - Stale Tasks Recovered: {summary['recovered_stale_tasks']}")
    print(f"  - New Public ATS Jobs  : {summary['new_jobs_from_ats']}")
    print(f"  - Jobs Ingested        : {summary['new_jobs_ingested']}")
    print(f"  - Evaluated This Run   : {summary['evaluated_jobs']}")
    print("  - Discovery Sources:")
    for source, details in summary["discovery_sources"].items():
        print(f"      {source}: {details.get('status', 'unknown')}")
    print(f"  - Backup Created At    : {summary['backup_path']}")
    print(f"  - Daily Report Saved   : {summary['report_path']}")
    print("==================================================")

if __name__ == "__main__":
    main()
