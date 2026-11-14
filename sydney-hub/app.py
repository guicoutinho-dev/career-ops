"""Composition root: wires ports to adapters and starts the local service."""
from __future__ import annotations

import logging
import random
import sys
import time
from pathlib import Path

import uvicorn

from hub.adapters.maestri_agent import MaestriAgentClassifier
from hub.adapters.maestri_portal import MaestriPortalScraper
from hub.adapters.process import resolve_maestri
from hub.adapters.windows_toast import WindowsToastNotifier
from hub.api.app import AppDeps, create_app
from hub.classify.queue import AiReviewQueue
from hub.domain.urls import InvalidGroupUrl, normalize_group_url
from hub.pipeline.routine import RoutineRunner
from hub.scheduler.service import SchedulerService
from hub.scheduler.slots import parse_slots
from hub.settings import Settings, load_settings
from hub.storage.db import Database
from hub.storage.catalog import CatalogRepository
from hub.storage.groups import DuplicateGroup, GroupRepository
from hub.storage.posts import PostRepository
from hub.storage.runs import RunRepository
from web.build_dashboard import build as build_dashboard

ROOT = Path(__file__).resolve().parent


def seed_groups(groups: GroupRepository, urls: list[str]) -> None:
    if groups.list():
        return
    for raw in urls:
        try:
            groups.add(normalize_group_url(raw), now=time.time())
        except (InvalidGroupUrl, DuplicateGroup):
            logging.getLogger(__name__).warning("Skipping invalid seed group %r", raw)


def build(settings: Settings) -> tuple[object, SchedulerService]:
    db = Database(settings.resolve(settings.paths.db))
    web_dir = settings.resolve(settings.paths.web)
    build_dashboard(web_dir)
    groups, posts, runs = GroupRepository(db), PostRepository(db), RunRepository(db)
    catalog = CatalogRepository(db)
    catalog.import_legacy_seed(web_dir / 'seed' / 'jobs.json')
    catalog.sync_posts()
    seed_groups(groups, settings.seed_groups)

    maestri = resolve_maestri()
    scraper = MaestriPortalScraper(
        maestri=maestri, script_path=str(settings.resolve(settings.scrape.script_path)),
        portal=settings.scrape.portal,
    )
    ai = MaestriAgentClassifier(maestri=maestri, agent=settings.ai.agent)
    low, high = settings.scrape.delay_seconds
    runner = RoutineRunner(
        groups=groups, posts=posts, runs=runs, scraper=scraper,
        ai_queue=AiReviewQueue(posts, ai, batch_size=settings.ai.batch_size),
        notifier=WindowsToastNotifier(), delay_fn=lambda: random.uniform(low, high),
        posts_per_group=settings.scrape.posts_per_group,
        catalog=catalog,
    )
    scheduler = SchedulerService(runner, runs, parse_slots(settings.schedule))
    port = settings.server.port
    deps = AppDeps(
        groups=groups, posts=posts, runs=runs, catalog=catalog, runner=runner, scheduler=scheduler,
        web_dir=web_dir,
        cv_dir=ROOT.parent / "output",
        allowed_hosts={f"127.0.0.1:{port}", f"localhost:{port}"},
    )
    return create_app(deps), scheduler


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings(ROOT / "config" / "settings.yml")
    app, scheduler = build(settings)
    scheduler.start()
    try:
        uvicorn.run(app, host=settings.server.host, port=settings.server.port, log_level="info")
    finally:
        scheduler.shutdown()


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    main()
