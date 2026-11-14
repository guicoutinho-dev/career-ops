import sqlite3
import json
import datetime
from typing import Optional, Any
from src.models.event import AuditEvent

class EventLogger:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def log_event(
        self,
        entity_type: str,
        entity_id: str,
        event_type: str,
        actor: str,
        correlation_id: str,
        payload: Optional[dict[str, Any]] = None,
        commit: bool = True,
    ) -> int:
        """Appends an immutable audit event to the events table."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        payload_str = json.dumps(payload) if payload else None

        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO events (entity_type, entity_id, event_type, actor, timestamp, correlation_id, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?);
        """, (entity_type, entity_id, event_type, actor, now_iso, correlation_id, payload_str))
        if commit:
            self.conn.commit()
        return cursor.lastrowid
