from __future__ import annotations

import sqlite3

from hub.domain.models import Group
from hub.storage.db import Database


class DuplicateGroup(Exception):
    """A group with this URL already exists."""


def _row_to_group(row: sqlite3.Row) -> Group:
    return Group(
        id=row["id"], url=row["url"], fb_group_id=row["fb_group_id"], name=row["name"],
        enabled=bool(row["enabled"]), last_run_at=row["last_run_at"], last_status=row["last_status"],
        last_new_posts=row["last_new_posts"], created_at=row["created_at"],
    )


class GroupRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, url: str, now: float) -> Group:
        try:
            with self._db.connect() as conn:
                cur = conn.execute("INSERT INTO groups(url, created_at) VALUES (?, ?)", (url, now))
                group_id = cur.lastrowid
        except sqlite3.IntegrityError as exc:
            raise DuplicateGroup(url) from exc
        return self.get(group_id)

    def get(self, group_id: int) -> Group | None:
        with self._db.connect() as conn:
            row = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
        return _row_to_group(row) if row else None

    def list(self, only_enabled: bool = False) -> list[Group]:
        sql = "SELECT * FROM groups" + (" WHERE enabled = 1" if only_enabled else "") + " ORDER BY id"
        with self._db.connect() as conn:
            return [_row_to_group(r) for r in conn.execute(sql).fetchall()]

    def set_enabled(self, group_id: int, enabled: bool) -> None:
        with self._db.connect() as conn:
            conn.execute("UPDATE groups SET enabled = ? WHERE id = ?", (int(enabled), group_id))

    def remove(self, group_id: int) -> bool:
        with self._db.connect() as conn:
            return conn.execute("DELETE FROM groups WHERE id = ?", (group_id,)).rowcount > 0

    def record_run_result(
        self, group_id: int, ts: float, status: str, new_posts: int,
        name: str | None = None, fb_group_id: str | None = None,
    ) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE groups SET last_run_at = ?, last_status = ?, last_new_posts = ?, "
                "name = COALESCE(?, name), fb_group_id = COALESCE(?, fb_group_id) WHERE id = ?",
                (ts, status, new_posts, name, fb_group_id, group_id),
            )
