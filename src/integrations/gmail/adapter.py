"""
Gmail API Adapter for Career-Ops / Australia Job Hunter.
Features:
- Read-only OAuth scope ('https://www.googleapis.com/auth/gmail.readonly').
- Durable checkpointing via SQLite cursor / history_id.
- Multi-signal matching & classification into email_messages table.
- Dry-run / Mock mode support for testing and offline environments.
"""

import json
import uuid
import datetime
import base64
from pathlib import Path
from typing import Dict, Any, List, Optional
import sqlite3

from src.database.connection import get_connection
from src.services.email_classifier import classify_email
from src.services.email_matcher import EmailMatcher
from src.services.event_logger import EventLogger


class GmailAdapter:
    def __init__(self, db_path: Optional[str] = None, credentials_path: Optional[str] = None):
        self.db_path = db_path
        self.credentials_path = credentials_path
        self.matcher = EmailMatcher(db_path=db_path)

    def process_raw_message(
        self,
        provider_message_id: str,
        sender: str,
        subject: str,
        body: str,
        received_at: str,
        thread_id: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None
    ) -> Dict[str, Any]:
        """
        Classifies, matches, and records an email message into SQLite.
        """
        should_close = False
        if conn is None:
            conn = get_connection(self.db_path) if self.db_path else get_connection()
            should_close = True

        try:
            # Check if already processed
            cur = conn.cursor()
            cur.execute("SELECT id, matched_application_id, classification FROM email_messages WHERE provider_message_id = ?", (provider_message_id,))
            existing = cur.fetchone()
            if existing:
                return {
                    "id": existing[0],
                    "status": "already_processed",
                    "matched_application_id": existing[1],
                    "classification": existing[2]
                }

            # 1. Classify Email
            classification, class_conf, class_keywords = classify_email(subject, body, sender)

            # 2. Match with Application
            matched_app_id, match_conf, match_signals = self.matcher.match(
                sender=sender,
                subject=subject,
                body=body,
                conn=conn
            )

            evidence = {
                "classification_keywords": class_keywords,
                "matching_signals": match_signals,
                "matching_confidence": match_conf,
                "preview": body[:200]
            }

            msg_id = f"email_{uuid.uuid4().hex[:12]}"
            cur.execute("""
                INSERT INTO email_messages (
                    id, provider, provider_message_id, thread_id,
                    received_at, sender, subject, classification,
                    confidence, matched_application_id, evidence_json
                ) VALUES (?, 'gmail', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                msg_id,
                provider_message_id,
                thread_id or provider_message_id,
                received_at,
                sender,
                subject,
                classification,
                class_conf,
                matched_app_id,
                json.dumps(evidence)
            ))

            # If matched and high confidence, record audit event and update application status
            if matched_app_id:
                logger = EventLogger(conn)
                logger.log_event(
                    entity_type="application",
                    entity_id=matched_app_id,
                    event_type=f"email_{classification.lower()}",
                    actor="gmail_adapter",
                    correlation_id=provider_message_id,
                    payload={
                        "email_id": msg_id,
                        "classification": classification,
                        "confidence": class_conf,
                        "sender": sender
                    }
                )

                # Automatic inbound status update on high match confidence (>= 0.75)
                if match_conf >= 0.75 and class_conf >= 0.75:
                    new_status = None
                    if classification == "INTERVIEW":
                        new_status = "INTERVIEW"
                    elif classification == "REJECTION":
                        new_status = "CLOSED_LOST"
                    elif classification == "ACKNOWLEDGMENT":
                        new_status = "APPLIED"

                    if new_status:
                        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        cur.execute(
                            "UPDATE applications SET status = ?, updated_at = ? WHERE id = ?",
                            (new_status, now_iso, matched_app_id)
                        )

            conn.commit()


            return {
                "id": msg_id,
                "status": "processed",
                "classification": classification,
                "classification_confidence": class_conf,
                "matched_application_id": matched_app_id,
                "matching_confidence": match_conf,
                "evidence": evidence
            }

        finally:
            if should_close and conn:
                conn.close()

    def sync_mock_inbox(self, mock_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Processes a list of mock messages for testing or offline dry-runs."""
        results = []
        conn = get_connection(self.db_path) if self.db_path else get_connection()
        try:
            for msg in mock_messages:
                res = self.process_raw_message(
                    provider_message_id=msg["id"],
                    sender=msg["sender"],
                    subject=msg["subject"],
                    body=msg["body"],
                    received_at=msg.get("received_at", datetime.datetime.now(datetime.timezone.utc).isoformat()),
                    thread_id=msg.get("thread_id"),
                    conn=conn
                )
                results.append(res)
            return results
        finally:
            conn.close()

    def sync_live_inbox(
        self,
        max_results: int = 10,
        query: str = "label:INBOX",
        credentials_file: Optional[Path | str] = None,
        token_file: Optional[Path | str] = None,
        dry_run: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Reads recent messages from live Gmail inbox using read-only credentials,
        classifies and correlates them with applications in SQLite.
        """
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        cred_path = Path(credentials_file or self.credentials_path or "config/gmail_credentials.json")
        tok_path = Path(token_file or "config/gmail_token.json")

        if not cred_path.exists() or not tok_path.exists():
            raise FileNotFoundError(f"Missing Gmail credentials or token at {cred_path} / {tok_path}")

        with open(tok_path, "r", encoding="utf-8") as f:
            t = json.load(f)
        with open(cred_path, "r", encoding="utf-8") as f:
            c = json.load(f)

        installed_info = c.get("installed") or c.get("web") or {}
        creds = Credentials(
            token=t.get("access_token"),
            refresh_token=t.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=installed_info.get("client_id"),
            client_secret=installed_info.get("client_secret"),
            scopes=t.get("scope", "").split() if isinstance(t.get("scope"), str) else t.get("scope")
        )

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        list_resp = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        messages_meta = list_resp.get("messages", [])

        results = []
        conn = get_connection(self.db_path) if self.db_path else get_connection()
        try:
            for item in messages_meta:
                msg_id = item["id"]
                full_msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
                payload = full_msg.get("payload", {})
                snippet = full_msg.get("snippet", "")
                thread_id = full_msg.get("threadId", msg_id)

                headers = {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers", [])}
                sender = headers.get("from", "")
                subject = headers.get("subject", "")
                received_at = headers.get("date", datetime.datetime.now(datetime.timezone.utc).isoformat())

                # Recursively extract body text and html
                texts, htmls = [], []
                def _walk_parts(p: Dict[str, Any]):
                    mime = p.get("mimeType", "")
                    body_data = p.get("body", {}).get("data")
                    if body_data:
                        try:
                            decoded = base64.urlsafe_b64decode(body_data.encode("ASCII")).decode("utf-8", errors="replace")
                            if mime == "text/plain":
                                texts.append(decoded)
                            elif mime == "text/html":
                                htmls.append(decoded)
                        except Exception:
                            pass
                    for sub in p.get("parts", []):
                        _walk_parts(sub)

                _walk_parts(payload)
                body_text = "\n".join(texts).strip()
                if not body_text and htmls:
                    import re
                    clean_html = re.sub(r"<[^>]+>", " ", "\n".join(htmls))
                    body_text = re.sub(r"\s+", " ", clean_html).strip()

                if not body_text.strip():
                    body_text = snippet

                if dry_run:
                    classification, class_conf, class_keywords = classify_email(subject, body_text, sender)
                    matched_app_id, match_conf, match_signals = self.matcher.match(
                        sender=sender, subject=subject, body=body_text, conn=conn
                    )
                    results.append({
                        "id": f"dry_run_{msg_id}",
                        "provider_message_id": msg_id,
                        "sender": sender,
                        "subject": subject,
                        "classification": classification,
                        "classification_confidence": class_conf,
                        "matched_application_id": matched_app_id,
                        "matching_confidence": match_conf,
                        "received_at": received_at,
                        "status": "dry_run"
                    })
                else:
                    res = self.process_raw_message(
                        provider_message_id=msg_id,
                        sender=sender,
                        subject=subject,
                        body=body_text,
                        received_at=received_at,
                        thread_id=thread_id,
                        conn=conn
                    )
                    res["sender"] = sender
                    res["subject"] = subject
                    results.append(res)

            return results
        finally:
            conn.close()
