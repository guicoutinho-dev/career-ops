from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from hub.domain.models import AiResult, AiStatus, Classification, Post, ScrapedPost
from hub.storage.db import Database


def _row_to_post(row: sqlite3.Row) -> Post:
    scraped = ScrapedPost(
        post_id=row["post_id"], group_url=row["group_url"], permalink=row["permalink"],
        body=row["body"], created_ts=row["created_ts"], author=row["author"] or "",
        author_url=row["author_url"] or "", group_name=row["group_name"] or "",
        photos=json.loads(row["photos"]), comment_count=row["comment_count"], reactions=row["reactions"],
    )
    ai = AiResult(**json.loads(row["ai_json"])) if row["ai_json"] else None
    return Post(
        scraped=scraped, classification=Classification(**json.loads(row["classification"])),
        group_id=row["group_id"], ai_status=AiStatus(row["ai_status"]), ai=ai,
        first_seen_at=row["first_seen_at"], is_new=bool(row["is_new"]), user_status=row["user_status"],
    )


class PostRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def known_ids(self, post_ids: list[str]) -> set[str]:
        if not post_ids:
            return set()
        marks = ",".join("?" * len(post_ids))
        with self._db.connect() as conn:
            rows = conn.execute(f"SELECT post_id FROM posts WHERE post_id IN ({marks})", post_ids).fetchall()
        return {r["post_id"] for r in rows}

    def add(self, post: Post) -> bool:
        s, c = post.scraped, post.classification
        with self._db.connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO posts(post_id, group_id, group_url, group_name, permalink, author, "
                "author_url, body, created_ts, photos, comment_count, reactions, first_seen_at, kind, "
                "classification, score, ai_status, ai_json, is_new, user_status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    s.post_id, post.group_id, s.group_url, s.group_name, s.permalink, s.author, s.author_url,
                    s.body, s.created_ts, json.dumps(s.photos), s.comment_count, s.reactions,
                    post.first_seen_at, c.kind, json.dumps(asdict(c)), c.score, post.ai_status.value,
                    json.dumps(asdict(post.ai)) if post.ai else None, int(post.is_new), post.user_status,
                ),
            )
            return cur.rowcount > 0

    def get(self, post_id: str) -> Post | None:
        with self._db.connect() as conn:
            row = conn.execute("SELECT * FROM posts WHERE post_id = ?", (post_id,)).fetchone()
        return _row_to_post(row) if row else None

    def list(self, kind: str | None = None, only_new: bool = False, limit: int = 500) -> list[Post]:
        where, params = ["kind != 'other'"], []
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if only_new:
            where.append("is_new = 1")
        sql = f"SELECT * FROM posts WHERE {' AND '.join(where)} ORDER BY created_ts DESC LIMIT ?"
        with self._db.connect() as conn:
            return [_row_to_post(r) for r in conn.execute(sql, [*params, limit]).fetchall()]

    def count_new(self) -> int:
        with self._db.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM posts WHERE is_new = 1 AND kind != 'other'").fetchone()[0]

    def mark_all_seen(self) -> None:
        with self._db.connect() as conn:
            conn.execute("UPDATE posts SET is_new = 0 WHERE is_new = 1")

    def set_user_status(self, post_id: str, status: str) -> bool:
        with self._db.connect() as conn:
            return conn.execute(
                "UPDATE posts SET user_status = ? WHERE post_id = ?", (status, post_id)
            ).rowcount > 0

    def pending_ai(self, limit: int = 20) -> list[Post]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM posts WHERE ai_status = 'pending' AND kind != 'other' "
                "ORDER BY score DESC, created_ts DESC LIMIT ?", (limit,),
            ).fetchall()
        return [_row_to_post(r) for r in rows]

    def count_pending_ai(self) -> int:
        with self._db.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM posts WHERE ai_status = 'pending' AND kind != 'other'"
            ).fetchone()[0]

    def save_ai(self, post_id: str, result: AiResult) -> None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE posts SET ai_status = 'done', ai_json = ? WHERE post_id = ?",
                (json.dumps(asdict(result)), post_id),
            )
