"""Facebook-free local web service & CLI entrypoint for Published Jobs (isolated DB)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hub.api.app import AppDeps, _spawn_thread, create_app
from hub.discovery.health import SourceHealthStore
from hub.discovery.pilot_gate import evaluate_pilot_gates
from hub.scheduler.jobs_scheduler import PublishedSchedulerService
from hub.settings import JobsConfig, ServerSettings, load_jobs_settings
from hub.storage.catalog import CatalogRepository
from hub.storage.db import Database, ensure_published_schema, validate_isolated_jobs_db_path
from hub.storage.jobs_bootstrap import bootstrap_isolated_jobs_db


ROOT = Path(__file__).resolve().parent


class _DisabledGroupsRepo:
    def list(self) -> list:
        return []

    def add(self, url: str, *, now: float):  # pragma: no cover - blocked by jobs_only guard
        raise RuntimeError("Facebook groups disabled in jobs-only mode")

    def get(self, group_id: int):  # pragma: no cover - blocked by jobs_only guard
        return None

    def set_enabled(self, group_id: int, enabled: bool) -> None:  # pragma: no cover
        raise RuntimeError("Facebook groups disabled in jobs-only mode")

    def remove(self, group_id: int) -> bool:  # pragma: no cover
        raise RuntimeError("Facebook groups disabled in jobs-only mode")


class _DisabledPostsRepo:
    def list(self, *, kind: str | None = None, only_new: bool = False) -> list:
        return []

    def count_new(self) -> int:
        return 0

    def count_pending_ai(self) -> int:
        return 0

    def mark_all_seen(self) -> None:  # pragma: no cover - blocked by jobs_only guard
        raise RuntimeError("Facebook posts disabled in jobs-only mode")

    def set_user_status(self, post_id: str, status: str) -> bool:  # pragma: no cover
        raise RuntimeError("Facebook posts disabled in jobs-only mode")

    def get(self, post_id: str):  # pragma: no cover
        return None


class _DisabledRunsRepo:
    def latest(self):
        return None


class _JobsRunnerAdapter:
    def __init__(self, running_fn: Callable[[], bool]) -> None:
        self._running_fn = running_fn

    @property
    def is_running(self) -> bool:
        return bool(self._running_fn())

    def run(self, trigger):  # pragma: no cover - blocked by jobs_only guard
        raise RuntimeError("Facebook pipeline runner disabled in jobs-only mode")


def _allowed_hosts(host: str, port: int) -> set[str]:
    hosts = {host, f"{host}:{port}", "localhost", f"localhost:{port}"}
    if host in {"127.0.0.1", "localhost"}:
        hosts.update({"127.0.0.1", f"127.0.0.1:{port}"})
    return hosts


def _maybe_build_dashboard(web_dir: Path) -> None:
    if not (web_dir / "template.html").is_file():
        return
    import contextlib
    from web.build_dashboard import build as build_dashboard

    with contextlib.redirect_stdout(sys.stderr):
        build_dashboard(web_dir)


def build_jobs_service(
    config: JobsConfig,
    *,
    start_scheduler: bool = False,
    run_fn: Callable[..., dict] | None = None,
    spawn: Callable[[Callable[[], None]], None] | None = None,
    jobs_running_override: Callable[[], bool] | None = None,
) -> tuple[FastAPI, PublishedSchedulerService]:
    """Build the Facebook-free Jobs FastAPI app and fail-closed PublishedSchedulerService."""
    pj = config.published_jobs
    db_path = config.resolve(pj.db_path)
    isolated_root = config.resolve(pj.isolated_db_dir)
    validate_isolated_jobs_db_path(db_path, isolated_root=isolated_root)
    ensure_published_schema(db_path, isolated_root=isolated_root)

    web_dir = config.resolve(config.paths.web)
    _maybe_build_dashboard(web_dir)

    db = Database(db_path)
    catalog = CatalogRepository(db)
    scheduler = PublishedSchedulerService(config, run_fn=run_fn)

    state_dir = config.resolve(pj.state_dir)
    reports_dir = config.resolve(pj.reports_dir)
    boards_path = config.resolve(pj.boards_path)
    policies_path = config.resolve(pj.policies_path)

    running_fn = jobs_running_override or (lambda: scheduler.is_running)

    def jobs_health() -> dict:
        gate = scheduler.gate_result
        scorecard = evaluate_pilot_gates(
            db_path,
            state_dir,
            reports_dir,
            boards_path,
            policies_path,
        )
        store = SourceHealthStore(state_dir)
        sources = store.load().get("sources", {})
        summary = catalog.summary()
        return {
            "mode": "jobs_only",
            "db_path": str(db_path),
            "isolated_root": str(isolated_root),
            "scheduler_active": scheduler.is_active,
            "scheduler_gate": {"allowed": gate.allowed, "reasons": list(gate.reasons)},
            "counts_by_state": summary.get("counts_by_state", {}),
            "lifecycle_counts": summary.get("lifecycle_counts", {}),
            "specified_work_counts": summary.get("specified_work_counts", {}),
            "sources": sources,
            "pilot_scorecard": scorecard.model_dump(mode="json"),
        }

    app = create_app(
        AppDeps(
            groups=_DisabledGroupsRepo(),
            posts=_DisabledPostsRepo(),
            runs=_DisabledRunsRepo(),
            catalog=catalog,
            runner=_JobsRunnerAdapter(running_fn),
            scheduler=scheduler,
            web_dir=web_dir,
            cv_dir=None,
            candidate_root=ROOT.parent,
            allowed_hosts=_allowed_hosts(config.server.host, config.server.port),
            spawn=spawn or _spawn_thread,
            jobs_only=True,
            jobs_health_fn=jobs_health,
            jobs_runner_fn=scheduler.trigger_manual,
            jobs_running_fn=running_fn,
        )
    )
    if start_scheduler:
        scheduler.start()
    return app, scheduler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run isolated Facebook-free SydneyStays Jobs service")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "jobs.yml")
    parser.add_argument("--bootstrap-from", type=Path, default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)

    cfg = load_jobs_settings(args.config)
    if args.host is not None or args.port is not None:
        cfg.server = ServerSettings(
            host=args.host or cfg.server.host,
            port=args.port or cfg.server.port,
        )

    if args.bootstrap_from is not None:
        pj = cfg.published_jobs
        bootstrap_isolated_jobs_db(
            args.bootstrap_from,
            cfg.resolve(pj.db_path),
            isolated_root=cfg.resolve(pj.isolated_db_dir),
        )

    app, scheduler = build_jobs_service(cfg, start_scheduler=not args.once)
    try:
        if args.once:
            client = TestClient(app, base_url=f"http://{cfg.server.host}:{cfg.server.port}")
            resp = client.get("/api/jobs/health")
            resp.raise_for_status()
            print(json.dumps(resp.json(), indent=2, sort_keys=True))
            return 0
        uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="info")
    finally:
        scheduler.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
