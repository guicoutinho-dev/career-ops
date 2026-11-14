"""
CLI for syncing Gmail messages, matching them with applications, and updating status.
Usage:
    python -m src.cli.gmail_sync --mock
    python -m src.cli.gmail_sync --list
"""

import argparse
import json
import sys
import time
import datetime
from typing import List, Dict, Any
from src.integrations.gmail.adapter import GmailAdapter
from src.database.connection import get_connection


MOCK_TEST_INBOX = [
    {
        "id": "gmail_msg_001",
        "sender": "talent@experis.com.au",
        "subject": "Interview Invitation: SAP ABAP Developer - Experis AU",
        "body": "Hi Guilherme,\nWe were very impressed by your profile and would like to invite you to a first round interview via Microsoft Teams next Tuesday.",
        "received_at": "2026-10-27T08:30:00Z"
    },
    {
        "id": "gmail_msg_002",
        "sender": "no-reply@gwf.com.au",
        "subject": "Your application at George Weston Foods - Senior SAP BTP Integration Developer",
        "body": "Thank you for applying for the Senior SAP BTP Integration Developer role. We are confirming receipt of your application.",
        "received_at": "2026-10-27T09:15:00Z"
    },
    {
        "id": "gmail_msg_003",
        "sender": "careers@sladegroup.com.au",
        "subject": "Update on your application for SAP ABAP Developer",
        "body": "Dear Guilherme,\nThank you for taking the time to apply. Unfortunately, after careful review, we have decided to pursue other candidates whose skills align more closely at this time.",
        "received_at": "2026-10-27T10:00:00Z"
    }
]

def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(description="Gmail Sync & Inbound Email Classifier CLI")
    parser.add_argument("--mock", action="store_true", help="Ingest built-in sample emails (interview, acknowledgment, rejection)")
    parser.add_argument("--live", action="store_true", help="Ingest and classify recent emails from live Gmail (read-only)")
    parser.add_argument("--watch", action="store_true", help="Run continuously in background/foreground, polling Gmail periodically")
    parser.add_argument("--interval", type=int, default=15, help="Polling interval in minutes for --watch (default: 15)")
    parser.add_argument("--limit", type=int, default=10, help="Max emails to process (default: 10)")
    parser.add_argument("--query", type=str, default="label:INBOX", help="Gmail search query filter (default: 'label:INBOX')")
    parser.add_argument("--list", action="store_true", help="List all classified emails from database")
    parser.add_argument("--dry-run", action="store_true", help="Run without persisting changes")

    args = parser.parse_args()
    adapter = GmailAdapter()

    if args.mock:
        print("==================================================")
        print("      GMAIL SYNC — PROCESSING INBOX SAMPLES       ")
        print("==================================================")
        results = adapter.sync_mock_inbox(MOCK_TEST_INBOX)
        for r in results:
            print(f"Message ID: {r['id']}")
            print(f"  - Status        : {r['status']}")
            print(f"  - Classification: {r['classification']} (conf: {r.get('classification_confidence')})")
            print(f"  - Matched App ID: {r['matched_application_id']} (conf: {r.get('matching_confidence')})")
            if r.get("evidence"):
                print(f"  - Signals       : {r['evidence'].get('matching_signals')}")
            print("--------------------------------------------------")
        print("[SUCCESS] Mock inbox processing complete.")

    elif args.live:
        print("==================================================")
        print("      GMAIL SYNC — LIVE INBOX INGESTION (READ-ONLY) ")
        print("==================================================")
        print(f"Query: {args.query} | Limit: {args.limit} | Dry-run: {args.dry_run}")
        try:
            results = adapter.sync_live_inbox(
                max_results=args.limit,
                query=args.query,
                dry_run=args.dry_run
            )
            print(f"Fetched {len(results)} message(s) from Gmail.")
            print("-" * 50)
            for r in results:
                print(f"Subject : {r.get('subject')}")
                print(f"From    : {r.get('sender')}")
                print(f"Class   : {r.get('classification')} (confidence: {r.get('classification_confidence')})")
                print(f"App ID  : {r.get('matched_application_id')} (confidence: {r.get('matching_confidence')})")
                print(f"Status  : {r.get('status')}")
                print("-" * 50)
            print("[SUCCESS] Live Gmail sync completed.")
        except Exception as e:
            print(f"[ERROR] Live Gmail sync failed: {e}")
            sys.exit(1)

    elif args.watch:
        print("==================================================")
        print(f"      GMAIL MONITOR — MODO WATCH ATIVO ({args.interval}m)")
        print("==================================================")
        from src.integrations.notion.adapter import NotionAdapter
        notion_adapter = NotionAdapter()
        try:
            while True:
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[{now_str}] Verificando novas mensagens no Gmail (Query: '{args.query}')...")
                results = adapter.sync_live_inbox(
                    max_results=args.limit,
                    query=args.query,
                    dry_run=args.dry_run
                )
                new_items = [r for r in results if r.get("status") == "processed"]
                if new_items:
                    print(f"[{now_str}] {len(new_items)} nova(s) mensagem(ns) processada(s)!")
                    for r in new_items:
                        app_id = r.get("matched_application_id")
                        print(f"  -> [{r.get('classification')}] App: {app_id} | Assunto: {r.get('subject')}")
                        if app_id and not args.dry_run:
                            ok, page_id = notion_adapter.sync_application(app_id)
                            print(f"     [Notion] Atualizado card: {'OK' if ok else 'Falha'} ({page_id})")
                else:
                    print(f"[{now_str}] Nenhuma mensagem nova.")

                print(f"Aguardando {args.interval} minuto(s)... (Pressione Ctrl+C para encerrar)\n")
                time.sleep(args.interval * 60)
        except KeyboardInterrupt:
            print("\n[INFO] Monitoramento encerrado pelo operador.")

    elif args.list:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT id, sender, subject, classification, confidence, matched_application_id, received_at
            FROM email_messages
            ORDER BY received_at DESC
        """)
        rows = cur.fetchall()
        conn.close()

        print("==================================================")
        print(f"      INBOUND EMAILS ARCHIVE ({len(rows)} messages)")
        print("==================================================")
        if not rows:
            print("[INFO] No email messages recorded yet.")
            return

        for r in rows:
            print(f"[{r[3]}] (conf: {r[4]}) {r[2]}")
            print(f"  From: {r[1]} | App: {r[5]} | Date: {r[6]}")
            print("-" * 50)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
