from __future__ import annotations

import re
import mimetypes
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Literal, Protocol

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from hub.api.schemas import GroupIn, GroupOut, GroupPatch, PostPatch
from hub.domain.models import Group, Post, Run, RunStatus, Trigger
from hub.domain.urls import InvalidGroupUrl, normalize_group_url
from hub.pipeline.routine import RunAlreadyActive
from hub.storage.groups import DuplicateGroup, GroupRepository
from hub.storage.catalog import CatalogRepository
from hub.storage.posts import PostRepository
from hub.storage.runs import RunRepository

mimetypes.add_type("image/webp", ".webp")


class RunnerLike(Protocol):
    @property
    def is_running(self) -> bool: ...
    def run(self, trigger: Trigger) -> Run: ...


class SchedulerLike(Protocol):
    def next_run_at(self) -> datetime: ...


def _spawn_thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True, name="manual-run").start()


@dataclass
class AppDeps:
    groups: GroupRepository
    posts: PostRepository
    runs: RunRepository
    catalog: CatalogRepository
    runner: RunnerLike
    scheduler: SchedulerLike
    web_dir: Path
    cv_dir: Path | None = None
    allowed_hosts: set[str] = field(default_factory=set)
    spawn: Callable[[Callable[[], None]], None] = _spawn_thread
    clock: Callable[[], float] = time.time


_CV_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]+\.(?:pdf|html)$")


def _group_out(g: Group) -> dict:
    return GroupOut(
        id=g.id, url=g.url, name=g.name, enabled=g.enabled, last_run_at=g.last_run_at,
        last_status=g.last_status, last_new_posts=g.last_new_posts,
    ).model_dump()


def _run_out(run: Run | None) -> dict | None:
    if run is None:
        return None
    return {
        "id": run.id, "trigger": run.trigger.value, "status": run.status.value,
        "started_at": run.started_at, "finished_at": run.finished_at, "groups_ok": run.groups_ok,
        "groups_failed": run.groups_failed, "new_posts": run.new_posts, "error": run.error,
    }


def _post_out(p: Post) -> dict:
    c, s = p.classification, p.scraped
    return {
        "id": s.post_id, "kind": (p.ai.kind if p.ai and p.ai.kind else c.kind),
        "group_name": s.group_name, "group_url": s.group_url, "permalink": s.permalink,
        "author": s.author, "author_url": s.author_url, "body": s.body, "created_ts": s.created_ts,
        "photos": s.photos, "comment_count": s.comment_count, "reactions": s.reactions,
        "price_week": c.price_week, "pay_hourly": c.pay_hourly, "suburb": c.suburb, "ticket": c.ticket,
        "phone": c.phone, "bills_included": c.bills_included, "has_ensuite": c.has_ensuite,
        "scam_flags": c.scam_flags, "score": c.score, "ai_status": p.ai_status.value,
        "ai": ({
            "fit_score": p.ai.fit_score, "summary": p.ai.summary,
            "scam_suspected": p.ai.scam_suspected, "extracted": p.ai.extracted,
        } if p.ai else None),
        "is_new": p.is_new, "user_status": p.user_status, "first_seen_at": p.first_seen_at,
    }


def create_app(deps: AppDeps) -> FastAPI:
    app = FastAPI(title="Sydney Hub", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = request.headers.get("host", "")
        if deps.allowed_hosts and host not in deps.allowed_hosts:
            return JSONResponse({"detail": "Forbidden host"}, status_code=403)
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
            allowed = {f"http://{h}" for h in deps.allowed_hosts}
            if deps.allowed_hosts and origin not in allowed:
                return JSONResponse({"detail": "Forbidden origin"}, status_code=403)
        return await call_next(request)

    # groups -----------------------------------------------------------------------------
    @app.get("/api/groups")
    def list_groups() -> list[dict]:
        return [_group_out(g) for g in deps.groups.list()]

    @app.post("/api/groups", status_code=201)
    def add_group(body: GroupIn) -> dict:
        try:
            url = normalize_group_url(body.url)
        except InvalidGroupUrl as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return _group_out(deps.groups.add(url, now=deps.clock()))
        except DuplicateGroup as exc:
            raise HTTPException(409, "This group is already on the list") from exc

    @app.patch("/api/groups/{group_id}")
    def patch_group(group_id: int, body: GroupPatch) -> dict:
        if deps.groups.get(group_id) is None:
            raise HTTPException(404, "Group not found")
        deps.groups.set_enabled(group_id, body.enabled)
        return _group_out(deps.groups.get(group_id))

    @app.delete("/api/groups/{group_id}", status_code=204)
    def delete_group(group_id: int) -> Response:
        if not deps.groups.remove(group_id):
            raise HTTPException(404, "Group not found")
        return Response(status_code=204)

    # runs & status ----------------------------------------------------------------------
    @app.post("/api/runs", status_code=202)
    def run_now() -> dict:
        if deps.runner.is_running:
            raise HTTPException(409, "A routine is already running")

        def job() -> None:
            try:
                deps.runner.run(Trigger.MANUAL)
            except RunAlreadyActive:
                pass

        deps.spawn(job)
        return {"status": "started"}

    @app.get("/api/status")
    def status() -> dict:
        latest = deps.runs.latest()
        groups = deps.groups.list()
        if deps.runner.is_running:
            state = "running"
        elif latest and latest.status == RunStatus.NEEDS_ATTENTION:
            state = "needs_attention"
        elif latest and latest.status == RunStatus.FAILED:
            state = "failed"
        else:
            state = "idle"
        return {
            "state": state,
            "next_run_at": deps.scheduler.next_run_at().isoformat(),
            "last_run": _run_out(latest),
            "new_posts": deps.posts.count_new(),
            "ai_pending": deps.posts.count_pending_ai(),
            "groups_total": len(groups),
            "groups_enabled": sum(1 for g in groups if g.enabled),
        }

    # posts ------------------------------------------------------------------------------
    @app.get('/api/opportunities')
    def list_opportunities(
        role: str | None = None, suburb: str | None = None, location: str | None = None,
        source: str | None = None,
        verification_state: Literal['verified_job', 'unverified_job_post', 'employer_lead'] | None = None,
        employment_type: str | None = None, experience: str | None = None,
        requirement: str | None = None, origin_suburb: str | None = None,
        max_commute_minutes: int | None = Query(None, ge=0, le=300),
        limit: int = Query(500, ge=1, le=500),
    ) -> list[dict]:
        if max_commute_minutes is not None and not origin_suburb:
            raise HTTPException(422, 'origin_suburb is required for a commute filter')
        return deps.catalog.list(
            role=role, suburb=suburb, location=location, source=source, verification_state=verification_state,
            employment_type=employment_type, experience=experience, requirement=requirement,
            origin_suburb=origin_suburb, max_commute_minutes=max_commute_minutes, limit=limit,
        )

    @app.get("/api/posts")
    def list_posts(kind: str | None = None, new: bool = False) -> list[dict]:
        if kind not in (None, "stay", "job"):
            raise HTTPException(422, "kind must be 'stay' or 'job'")
        posts = deps.posts.list(kind=kind, only_new=new)
        return [_post_out(p) for p in posts if not (p.ai and p.ai.kind == "other")]

    @app.post("/api/posts/seen", status_code=204)
    def mark_seen() -> Response:
        deps.posts.mark_all_seen()
        return Response(status_code=204)

    @app.patch("/api/posts/{post_id}")
    def patch_post(post_id: str, body: PostPatch) -> dict:
        if not deps.posts.set_user_status(post_id, body.user_status):
            raise HTTPException(404, "Post not found")
        return _post_out(deps.posts.get(post_id))

    @app.get("/api/cv/{filename}")
    def get_cv(filename: str) -> FileResponse:
        if not deps.cv_dir or not _CV_NAME_RE.match(filename):
            raise HTTPException(404, "CV not found")
        base = deps.cv_dir.resolve()
        target = (base / filename).resolve()
        if target.parent != base or not target.is_file():
            raise HTTPException(404, "CV not found")
        return FileResponse(target)

    if deps.web_dir.exists():
        app.mount("/", StaticFiles(directory=str(deps.web_dir), html=True), name="web")
    return app
