from __future__ import annotations

import sqlite3

from hub.domain.models import Run, RunStatus, Trigger
from hub.storage.db import Database


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"], trigger=Trigger(row["trigger"]), started_at=row["started_at"],
        finished_at=row["finished_at"], status=RunStatus(row["status"]), groups_ok=row["groups_ok"],
        groups_failed=row["groups_failed"], new_posts=row["new_posts"], error=row["error"],
    )


class RunRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def start(self, trigger: Trigger, now: float) -> Run:
        with self._db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO runs(started_at, trigger, status) VALUES (?, ?, ?)",
                (now, trigger.value, RunStatus.RUNNING.value),
            )
            run_id = cur.lastrowid
        return Run(trigger=trigger, started_at=now, id=run_id)

    def finish(self, run: Run) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE runs SET finished_at = ?, status = ?, groups_ok = ?, groups_failed = ?, "
                "new_posts = ?, error = ? WHERE id = ?",
                (run.finished_at, run.status.value, run.groups_ok, run.groups_failed,
                 run.new_posts, run.error, run.id),
            )

    def latest(self) -> Run | None:
        with self._db.connect() as conn:
            row = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        return _row_to_run(row) if row else None

    def last_finished_at(self, statuses: list[RunStatus]) -> float | None:
        marks = ",".join("?" * len(statuses))
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT MAX(finished_at) AS ts FROM runs WHERE status IN ({marks})",
                [s.value for s in statuses],
            ).fetchone()
        return row["ts"]
