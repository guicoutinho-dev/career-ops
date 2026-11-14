import sqlite3
import datetime
from typing import Optional, List
from src.models.task import TaskRecord

class TaskQueue:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def enqueue(self, task: TaskRecord) -> str:
        """Enqueues a task idempotently."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO tasks (
                id, task_type, entity_id, status, attempts, idempotency_key, scheduled_at, started_at, completed_at, error_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (
            task.id, task.task_type, task.entity_id, task.status, task.attempts,
            task.idempotency_key, task.scheduled_at, task.started_at, task.completed_at, task.error_code
        ))
        self.conn.commit()
        return task.id

    def claim_next_pending(self, task_type: Optional[str] = None) -> Optional[sqlite3.Row]:
        """Atomically claims the next pending task for execution."""
        cursor = self.conn.cursor()
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        query = "SELECT * FROM tasks WHERE status = 'PENDING'"
        params = []
        if task_type:
            query += " AND task_type = ?"
            params.append(task_type)
        query += " ORDER BY scheduled_at ASC LIMIT 1;"

        cursor.execute(query, params)
        row = cursor.fetchone()
        if not row:
            return None

        task_id = row["id"]
        cursor.execute("""
            UPDATE tasks 
            SET status = 'RUNNING', started_at = ?, attempts = attempts + 1
            WHERE id = ? AND status = 'PENDING';
        """, (now_iso, task_id))
        self.conn.commit()
        return row

    def complete(self, task_id: str) -> None:
        """Marks a task as completed."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE tasks 
            SET status = 'COMPLETED', completed_at = ?
            WHERE id = ?;
        """, (now_iso, task_id))
        self.conn.commit()

    def fail(self, task_id: str, error_code: str, allow_retry: bool = True, max_attempts: int = 3) -> None:
        """Marks a task as failed or schedules retry."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT attempts FROM tasks WHERE id = ?;", (task_id,))
        row = cursor.fetchone()
        attempts = row["attempts"] if row else 1

        if allow_retry and attempts < max_attempts:
            new_status = "RETRYING"
        else:
            new_status = "FAILED"

        cursor.execute("""
            UPDATE tasks 
            SET status = ?, error_code = ?
            WHERE id = ?;
        """, (new_status, error_code, task_id))
        self.conn.commit()

    def recover_stale_tasks(self, timeout_minutes: int = 15) -> int:
        """Recovers tasks stuck in RUNNING for longer than timeout_minutes."""
        cursor = self.conn.cursor()
        cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=timeout_minutes)).isoformat()
        cursor.execute("""
            UPDATE tasks
            SET status = 'PENDING', started_at = NULL
            WHERE status = 'RUNNING' AND started_at < ?;
        """, (cutoff,))
        recovered_count = cursor.rowcount
        self.conn.commit()
        return recovered_count
