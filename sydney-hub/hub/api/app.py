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
from hub.career.prep import load_candidate_facts, prepare_application_packet
from hub.domain.matching import CandidateProfile, evaluate_opportunity_match
from hub.domain.models import Group, Post, Run, RunStatus, Trigger
from hub.domain.specified_work_462 import classify_specified_work_462
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
    def next_run_at(self) -> datetime | None: ...


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
    candidate_root: Path | None = None
    allowed_hosts: set[str] = field(default_factory=set)
    spawn: Callable[[Callable[[], None]], None] = _spawn_thread
    clock: Callable[[], float] = time.time
    jobs_only: bool = False
    jobs_health_fn: Callable[[], dict] | None = None
    jobs_runner_fn: Callable[[], dict] | None = None
    jobs_running_fn: Callable[[], bool] | None = None


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

    def _require_facebook_enabled() -> None:
        if deps.jobs_only:
            raise HTTPException(409, "Facebook pipeline disabled in jobs-only mode")

    @app.post("/api/groups", status_code=201)
    def add_group(body: GroupIn) -> dict:
        _require_facebook_enabled()
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
        _require_facebook_enabled()
        if deps.groups.get(group_id) is None:
            raise HTTPException(404, "Group not found")
        deps.groups.set_enabled(group_id, body.enabled)
        return _group_out(deps.groups.get(group_id))

    @app.delete("/api/groups/{group_id}", status_code=204)
    def delete_group(group_id: int) -> Response:
        _require_facebook_enabled()
        if not deps.groups.remove(group_id):
            raise HTTPException(404, "Group not found")
        return Response(status_code=204)

    # runs & status ----------------------------------------------------------------------
    @app.post("/api/runs", status_code=202)
    def run_now() -> dict:
        _require_facebook_enabled()
        if deps.runner.is_running:
            raise HTTPException(409, "A routine is already running")

        def job() -> None:
            try:
                deps.runner.run(Trigger.MANUAL)
            except RunAlreadyActive:
                pass

        deps.spawn(job)
        return {"status": "started"}

    @app.get("/api/jobs/health")
    def jobs_health() -> dict:
        if deps.jobs_health_fn is not None:
            return deps.jobs_health_fn()
        return {
            "mode": "jobs_only" if deps.jobs_only else "mixed",
            "scheduler_active": False,
            "sources": {},
            "pilot_scorecard": None,
        }

    @app.post("/api/jobs/runs", status_code=202)
    def jobs_run_now() -> dict:
        if deps.jobs_runner_fn is None:
            raise HTTPException(409, "Published jobs manual runner is not configured")
        if deps.jobs_running_fn and deps.jobs_running_fn():
            raise HTTPException(409, "A published jobs run is already running")

        def job() -> None:
            try:
                deps.jobs_runner_fn()
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
        nxt = deps.scheduler.next_run_at()
        return {
            "state": state,
            "next_run_at": nxt.isoformat() if nxt is not None else None,
            "last_run": _run_out(latest),
            "new_posts": deps.posts.count_new(),
            "ai_pending": deps.posts.count_pending_ai(),
            "groups_total": len(groups),
            "groups_enabled": sum(1 for g in groups if g.enabled),
        }

    def _opportunity_filters(
        *,
        role: str | None = None,
        suburb: str | None = None,
        location: str | None = None,
        source: str | None = None,
        verification_state: Literal['verified_job', 'unverified_job_post', 'employer_lead'] | None = None,
        employment_type: str | None = None,
        experience: str | None = None,
        requirement: str | None = None,
        origin_suburb: str | None = None,
        max_commute_minutes: int | None = None,
        role_bucket: str | None = None,
        recent: bool | None = None,
        commute_window: Literal['tuesday_0900', 'friday_1800'] | None = None,
        commute_unknown: bool | None = None,
        pay_min: float | None = None,
        pay_max: float | None = None,
        include_unknown_pay: bool = False,
        include_unknown_employment_type: bool = False,
        include_unknown_requirement: bool = False,
        category: str | None = None,
        ats_provider: str | None = None,
        recent_days: int | None = None,
        lifecycle_state: Literal['active', 'stale', 'expired'] | None = None,
        include_expired: bool = True,
    ) -> dict:
        if max_commute_minutes is not None and not origin_suburb:
            raise HTTPException(422, 'origin_suburb is required for a commute filter')
        if commute_unknown is not None and (not origin_suburb or not commute_window):
            raise HTTPException(422, 'origin_suburb and commute_window are required for commute_unknown')
        if pay_min is not None and pay_max is not None and pay_min > pay_max:
            raise HTTPException(422, 'pay_min must be <= pay_max')
        return {
            'role': role,
            'suburb': suburb,
            'location': location,
            'source': source,
            'verification_state': verification_state,
            'employment_type': employment_type,
            'experience': experience,
            'requirement': requirement,
            'origin_suburb': origin_suburb,
            'max_commute_minutes': max_commute_minutes,
            'role_bucket': role_bucket,
            'recent': recent,
            'commute_window': commute_window,
            'commute_unknown': commute_unknown,
            'pay_min': pay_min,
            'pay_max': pay_max,
            'include_unknown_pay': include_unknown_pay,
            'include_unknown_employment_type': include_unknown_employment_type,
            'include_unknown_requirement': include_unknown_requirement,
            'category': category,
            'ats_provider': ats_provider,
            'recent_days': recent_days,
            'lifecycle_state': lifecycle_state,
            'include_expired': include_expired,
        }

    def _enrich_item(item: dict, profile: CandidateProfile) -> dict:
        enriched = dict(item)
        enriched['match'] = evaluate_opportunity_match(enriched, profile).to_dict()
        enriched['specified_work_462'] = classify_specified_work_462(enriched).to_dict()
        return enriched

    # posts ------------------------------------------------------------------------------
    @app.get('/api/opportunities')
    def list_opportunities(
        response: Response,
        role: str | None = None, suburb: str | None = None, location: str | None = None,
        source: str | None = None,
        verification_state: Literal['verified_job', 'unverified_job_post', 'employer_lead'] | None = None,
        employment_type: str | None = None, experience: str | None = None,
        requirement: str | None = None, origin_suburb: str | None = None,
        max_commute_minutes: int | None = Query(None, ge=0, le=300),
        role_bucket: str | None = None, recent: bool | None = None,
        commute_window: Literal['tuesday_0900', 'friday_1800'] | None = None,
        commute_unknown: bool | None = None,
        pay_min: float | None = Query(None, ge=0),
        pay_max: float | None = Query(None, ge=0),
        include_unknown_pay: bool = False,
        include_unknown_employment_type: bool = False,
        include_unknown_requirement: bool = False,
        category: str | None = None,
        ats_provider: str | None = None,
        recent_days: int | None = Query(None, ge=1, le=365),
        lifecycle_state: Literal['active', 'stale', 'expired'] | None = None,
        include_expired: bool = True,
        view: Literal['all', '88_days'] = 'all',
        specified_work_status: Literal['eligible', 'ineligible', 'needs_review'] | None = None,
        limit: int = Query(500, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> list[dict]:
        filters = _opportunity_filters(
            role=role, suburb=suburb, location=location, source=source, verification_state=verification_state,
            employment_type=employment_type, experience=experience, requirement=requirement,
            origin_suburb=origin_suburb, max_commute_minutes=max_commute_minutes,
            role_bucket=role_bucket, recent=recent, commute_window=commute_window,
            commute_unknown=commute_unknown, pay_min=pay_min, pay_max=pay_max,
            include_unknown_pay=include_unknown_pay,
            include_unknown_employment_type=include_unknown_employment_type,
            include_unknown_requirement=include_unknown_requirement,
            category=category, ats_provider=ats_provider, recent_days=recent_days,
            lifecycle_state=lifecycle_state, include_expired=include_expired,
        )
        profile = load_candidate_facts(deps.candidate_root).to_candidate_profile()
        if view == '88_days' or specified_work_status is not None:
            raw_rows = deps.catalog.list(**filters, limit=500, offset=0)
            enriched = [_enrich_item(row, profile) for row in raw_rows]
            if specified_work_status is not None:
                enriched = [
                    row for row in enriched
                    if row['specified_work_462']['status'] == specified_work_status
                ]
            elif view == '88_days':
                rank = {'eligible': 0, 'needs_review': 1, 'ineligible': 2}
                enriched = [
                    row for row in enriched
                    if row['specified_work_462']['status'] in ('eligible', 'needs_review')
                ]
                enriched.sort(
                    key=lambda row: (
                        rank.get(row['specified_work_462']['status'], 2),
                        -float(row['match'].get('score', 0.0)),
                    )
                )
            response.headers['X-Total-Count'] = str(len(enriched))
            return enriched[offset: offset + limit]

        total = deps.catalog.count(**filters)
        response.headers['X-Total-Count'] = str(total)
        rows = deps.catalog.list(**filters, limit=limit, offset=offset)
        return [_enrich_item(row, profile) for row in rows]

    @app.get('/api/opportunities/summary')
    def opportunities_summary(
        role: str | None = None, suburb: str | None = None, location: str | None = None,
        source: str | None = None,
        verification_state: Literal['verified_job', 'unverified_job_post', 'employer_lead'] | None = None,
        employment_type: str | None = None, experience: str | None = None,
        requirement: str | None = None, origin_suburb: str | None = None,
        max_commute_minutes: int | None = Query(None, ge=0, le=300),
        role_bucket: str | None = None, recent: bool | None = None,
        commute_window: Literal['tuesday_0900', 'friday_1800'] | None = None,
        commute_unknown: bool | None = None,
        pay_min: float | None = Query(None, ge=0),
        pay_max: float | None = Query(None, ge=0),
        include_unknown_pay: bool = False,
        include_unknown_employment_type: bool = False,
        include_unknown_requirement: bool = False,
        category: str | None = None,
        ats_provider: str | None = None,
        recent_days: int | None = Query(None, ge=1, le=365),
        lifecycle_state: Literal['active', 'stale', 'expired'] | None = None,
        include_expired: bool = True,
    ) -> dict:
        filters = _opportunity_filters(
            role=role, suburb=suburb, location=location, source=source, verification_state=verification_state,
            employment_type=employment_type, experience=experience, requirement=requirement,
            origin_suburb=origin_suburb, max_commute_minutes=max_commute_minutes,
            role_bucket=role_bucket, recent=recent, commute_window=commute_window,
            commute_unknown=commute_unknown, pay_min=pay_min, pay_max=pay_max,
            include_unknown_pay=include_unknown_pay,
            include_unknown_employment_type=include_unknown_employment_type,
            include_unknown_requirement=include_unknown_requirement,
            category=category, ats_provider=ats_provider, recent_days=recent_days,
            lifecycle_state=lifecycle_state, include_expired=include_expired,
        )
        summary_data = deps.catalog.summary(**filters)
        sw_counts = {'eligible': 0, 'needs_review': 0, 'ineligible': 0}
        for row in deps.catalog.list(**filters, limit=500, offset=0):
            status_462 = classify_specified_work_462(row).status
            if status_462 in sw_counts:
                sw_counts[status_462] += 1
        summary_data['specified_work_counts'] = sw_counts
        return summary_data

    @app.post('/api/opportunities/{opportunity_id}/prepare')
    def prepare_opportunity(opportunity_id: str) -> dict:
        opp = deps.catalog.get(opportunity_id)
        if opp is None:
            raise HTTPException(404, 'Opportunity not found')
        facts = load_candidate_facts(deps.candidate_root)
        return prepare_application_packet(opp, facts)

    @app.get("/api/posts")
    def list_posts(kind: str | None = None, new: bool = False) -> list[dict]:
        if kind not in (None, "stay", "job"):
            raise HTTPException(422, "kind must be 'stay' or 'job'")
        posts = deps.posts.list(kind=kind, only_new=new)
        return [_post_out(p) for p in posts if not (p.ai and p.ai.kind == "other")]

    @app.post("/api/posts/seen", status_code=204)
    def mark_seen() -> Response:
        _require_facebook_enabled()
        deps.posts.mark_all_seen()
        return Response(status_code=204)

    @app.patch("/api/posts/{post_id}")
    def patch_post(post_id: str, body: PostPatch) -> dict:
        _require_facebook_enabled()
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
