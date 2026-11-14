import os
import sys
import json
import sqlite3
import datetime
from pathlib import Path

# Ensure root is on python path
REPO_ROOT = Path("D:/AntigravityProjetos/career-ops")
sys.path.insert(0, str(REPO_ROOT))

from src.services.env import load_env
load_env(REPO_ROOT / ".env")

from src.integrations.notion.adapter import NotionAdapter

def reconcile_and_sync():
    conn = sqlite3.connect(REPO_ROOT / "data/applications.db")
    cur = conn.cursor()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    print("==================================================")
    print("   RECONCILING APPLICATIONS & SYNCING TO NOTION   ")
    print("==================================================")

    # 1. Update app_voyages_gardener_917579 to INTERVIEW
    cur.execute("SELECT status FROM applications WHERE id = 'app_voyages_gardener_917579'")
    row = cur.fetchone()
    if row:
        print(f"app_voyages_gardener_917579 current status: {row[0]}")
        cur.execute(
            "UPDATE applications SET status = 'INTERVIEW', updated_at = ? WHERE id = 'app_voyages_gardener_917579'",
            (now_iso,)
        )
        print("Updated app_voyages_gardener_917579 to 'INTERVIEW'")

    # 2. Reconcile REJECTION emails
    cur.execute("""
        SELECT e.id, e.sender, e.subject, e.confidence, e.matched_application_id, e.evidence_json, a.status
        FROM email_messages e
        JOIN applications a ON e.matched_application_id = a.id
        WHERE e.classification = 'REJECTION'
          AND a.status NOT IN ('CLOSED_LOST', 'REJECTED', 'DISQUALIFIED')
    """)
    rejection_rows = cur.fetchall()
    print(f"\nFound {len(rejection_rows)} applications with REJECTION emails pending CLOSED_LOST:")
    reconciled_rejections = []
    for r in rejection_rows:
        msg_id, sender, subject, conf, app_id, ev_json, cur_status = r
        try:
            ev = json.loads(ev_json or "{}")
            match_conf = ev.get("matching_confidence", 0.0)
        except Exception:
            match_conf = 0.0
        
        if (conf or 0) >= 0.75 and match_conf >= 0.70:
            print(f"  - Reconciling {app_id} ({cur_status} -> CLOSED_LOST) from '{sender}' (conf: {conf}, match: {match_conf})")
            cur.execute(
                "UPDATE applications SET status = 'CLOSED_LOST', updated_at = ? WHERE id = ?",
                (now_iso, app_id)
            )
            reconciled_rejections.append(app_id)
        else:
            print(f"  - Skipping {app_id}: low confidence (conf: {conf}, match: {match_conf})")

    conn.commit()

    # 3. Notion Sync for updated rows
    adapter = NotionAdapter()
    print(f"\nNotion Adapter ready (dry_run={adapter.dry_run}, database_id={adapter.database_id})")

    # Sync Housing Gardener first
    print("\n[Syncing] Housing Gardener (INTERVIEW):")
    ok, page_id = adapter.sync_application("app_voyages_gardener_917579")
    print(f"  Result: {ok} (page: {page_id})")

    # Sync Reconciled Rejections
    print(f"\n[Syncing] {len(reconciled_rejections)} Reconciled Rejections to Notion:")
    for app_id in reconciled_rejections:
        ok, res = adapter.sync_application(app_id)
        print(f"  - {app_id}: ok={ok} res={res}")

    # 4. Sync unmapped Applied applications (where notion_page_id is NULL)
    cur.execute("""
        SELECT id, status FROM applications 
        WHERE status IN ('APPLIED', '4. APLICADO (Candidatura Enviada)') 
          AND (notion_page_id IS NULL OR notion_page_id = '')
    """)
    unmapped_applied = cur.fetchall()
    print(f"\n[Syncing] {len(unmapped_applied)} unmapped APPLIED applications to Notion:")
    for app_id, st in unmapped_applied:
        ok, res = adapter.sync_application(app_id)
        print(f"  - {app_id} ({st}): ok={ok} page={res}")

    conn.close()
    print("\n==================================================")
    print("               RECONCILIATION COMPLETE            ")
    print("==================================================")

if __name__ == "__main__":
    reconcile_and_sync()
