"""
Backfills `category` and `visa_fit` for every application.
Usage: python -m src.cli.classify [--report]
"""

import argparse
from collections import Counter

from src.database.connection import get_connection
from src.database.migrations import apply_migrations
from src.services.job_classifier import classify_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify jobs by category and visa fit.")
    parser.add_argument("--report", action="store_true", help="Only print the distribution, do not write")
    args = parser.parse_args()

    apply_migrations()
    conn = get_connection()
    rows = conn.execute("""
        SELECT a.id, a.role_track, a.status, j.title, j.description, j.location
        FROM applications a JOIN jobs j ON a.job_id = j.id
    """).fetchall()

    dist = Counter()
    updates = []
    for r in rows:
        category, fit = classify_job(r["title"], r["description"], r["location"], r["role_track"])
        updates.append((category, fit, r["id"]))
        if r["status"] in ("QUALIFIED", "Evaluated"):
            dist[(category, fit)] += 1

    if not args.report:
        conn.executemany("UPDATE applications SET category = ?, visa_fit = ? WHERE id = ?;", updates)
        conn.commit()
    conn.close()

    print(f"[OK] {len(updates)} applications classified" + (" (report only)" if args.report else ""))
    if not args.report:
        from src.services.batch_evaluator import reevaluate_by_category
        changes = reevaluate_by_category()
        print(f"[OK] Re-scored with category catalogs: {changes}")
        conn = get_connection()
        dist = Counter()
        for r in conn.execute("SELECT category, visa_fit FROM applications WHERE status IN ('QUALIFIED','Evaluated')"):
            dist[(r["category"], r["visa_fit"])] += 1
        conn.close()
    print("Qualified/Evaluated distribution:")
    for (cat, fit), n in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"  - {cat:<24} {fit:<12} {n}")


if __name__ == "__main__":
    main()
