import sys
from src.database.connection import get_connection
from src.database.migrations import apply_migrations

def print_status():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    apply_migrations()
    conn = get_connection()
    cursor = conn.cursor()

    print("==================================================")
    print("        AUSTRALIA JOB HUNTER — SYSTEM STATUS      ")
    print("==================================================")

    # 1. Jobs count
    cursor.execute("SELECT COUNT(*) FROM jobs;")
    jobs_count = cursor.fetchone()[0]

    # 2. Applications count by status
    cursor.execute("SELECT status, COUNT(*) FROM applications GROUP BY status;")
    app_status_counts = cursor.fetchall()

    # 3. Pending approvals
    cursor.execute("SELECT COUNT(*) FROM approvals WHERE decision = 'PENDING';")
    pending_approvals = cursor.fetchone()[0]

    # 4. Tasks by status
    cursor.execute("SELECT status, COUNT(*) FROM tasks GROUP BY status;")
    task_counts = cursor.fetchall()

    print(f"Total Discovered Jobs : {jobs_count}")
    print("\nApplications by Status:")
    if not app_status_counts:
        print("  (None)")
    for row in app_status_counts:
        print(f"  - {row[0]}: {row[1]}")

    print(f"\nPending Human Approvals: {pending_approvals}")

    print("\nDurable Tasks:")
    if not task_counts:
        print("  (Queue Empty)")
    for row in task_counts:
        print(f"  - {row[0]}: {row[1]}")

    print("==================================================")

if __name__ == "__main__":
    print_status()
