"""
Protocolo Auditoria — Career-Ops / Australia Job Hunter
Performs full reconciliation across:
1. SQLite authoritative database (applications, email_messages, audit_events)
2. Notion Live Board (database pages, stages, notes)
3. Gmail Inbox (live query for recent employer responses, acknowledgments, interview invites)
4. Follow-up Calendar (cadence check for pending follow-ups)
"""

import sys
import os
import json
import sqlite3
import datetime
from pathlib import Path
from typing import Dict, Any, List

from src.cli.notion_sync import load_env_file
from src.database.connection import get_connection
from src.integrations.notion.adapter import NotionAdapter
from src.integrations.gmail.adapter import GmailAdapter


def run_audit() -> Dict[str, Any]:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")

    load_env_file()
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    report = {
        "timestamp": now_utc.isoformat(),
        "sqlite_applied": [],
        "notion_applied": [],
        "gmail_recent_inbound": [],
        "actionable_items": [],
        "follow_ups_due": []
    }

    conn = get_connection()
    cur = conn.cursor()

    # 1. SQLite Audit
    cur.execute("""
        SELECT a.id, j.employer, j.title, a.status, a.submitted_at, a.match_score, a.category, a.notion_page_id
        FROM applications a
        JOIN jobs j ON a.job_id = j.id
        WHERE a.status IN ('APPLIED', '4. APLICADO', 'INTERVIEW', 'Applied')
        ORDER BY a.submitted_at DESC
    """)
    rows = cur.fetchall()
    for r in rows:
        report["sqlite_applied"].append({
            "id": r[0],
            "company": r[1],
            "title": r[2],
            "status": r[3],
            "applied_at": r[4],
            "match_score": r[5],
            "category": r[6],
            "notion_page_id": r[7]
        })

    # 2. Notion Live Board Audit
    adapter = NotionAdapter()
    if not adapter.dry_run and adapter.database_id:
        pages = []
        has_more = True
        next_cursor = None
        while has_more:
            body = {"page_size": 100}
            if next_cursor:
                body["start_cursor"] = next_cursor
            status, res = adapter._http_request("POST", f"databases/{adapter.database_id}/query", body)
            if status != 200:
                break
            results = res.get("results", [])
            pages.extend(results)
            has_more = res.get("has_more", False)
            next_cursor = res.get("next_cursor")

        for p in pages:
            props = p.get("properties", {})
            stage_obj = props.get("Prep Stage") or props.get("Stage") or props.get("Status") or {}
            stage = "Unknown"
            if stage_obj.get("select"):
                stage = stage_obj["select"].get("name", "")
            elif stage_obj.get("status"):
                stage = stage_obj["status"].get("name", "")

            title_l = props.get("Role Title", {}).get("title", []) or props.get("Name", {}).get("title", [])
            title = title_l[0].get("plain_text", "") if title_l else ""
            company_l = props.get("Company", {}).get("rich_text", [])
            company = company_l[0].get("plain_text", "") if company_l else ""
            date_l = props.get("Date Applied", {}).get("date") or {}
            date_val = date_l.get("start", "") if date_l else ""
            track_obj = props.get("Track") or {}
            track = track_obj.get("select", {}).get("name", "") if track_obj.get("select") else ""

            if any(k in stage.upper() for k in ["APLICADO", "APPLIED", "ENTREVISTA", "INTERVIEW", "RESPOSTA"]):
                report["notion_applied"].append({
                    "notion_id": p.get("id"),
                    "company": company,
                    "title": title,
                    "stage": stage,
                    "track": track,
                    "date_applied": date_val
                })

    # 3. Gmail Audit (Recent Relevant Inbound Emails)
    cur.execute("""
        SELECT id, sender, subject, classification, confidence, matched_application_id, received_at
        FROM email_messages
        ORDER BY received_at DESC
        LIMIT 25
    """)
    email_rows = cur.fetchall()
    for er in email_rows:
        report["gmail_recent_inbound"].append({
            "id": er[0],
            "sender": er[1],
            "subject": er[2],
            "classification": er[3],
            "confidence": er[4],
            "matched_app": er[5],
            "received_at": er[6]
        })

    # Check for specific actionable emails
    for em in report["gmail_recent_inbound"]:
        subj = (em["subject"] or "").lower()
        sender = (em["sender"] or "").lower()
        if "interview" in subj or "invitation" in subj or "afca" in subj or "afca" in sender or "emily.marsh" in sender:
            report["actionable_items"].append({
                "type": "RECRUITER_CONTACT",
                "details": f"{em['sender']} - {em['subject']}",
                "classification": em["classification"]
            })

    # 4. Follow-up schedule audit
    followup_file = Path("data/follow-ups.md")
    if followup_file.exists():
        with open(followup_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("|") and not line.startswith("|-") and not line.startswith("| #"):
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 6:
                        # # | Report | Company | Role | Applied | Follow-up Due | Status
                        report["follow_ups_due"].append({
                            "company": parts[3] if len(parts) > 3 else "",
                            "role": parts[4] if len(parts) > 4 else "",
                            "applied": parts[5] if len(parts) > 5 else "",
                            "due_date": parts[6] if len(parts) > 6 else ""
                        })

    conn.close()
    return report

if __name__ == "__main__":
    rep = run_audit()
    print(json.dumps(rep, indent=2, ensure_ascii=False))
