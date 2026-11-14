from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable

from hub.classify.queue import AiReviewQueue
from hub.domain.models import (
    AiStatus, ChallengeDetected, Group, LoginRequired, Post, Run, RunStatus,
    ScrapedPost, ScrapeError, Trigger,
)
from hub.domain.rules import RELEVANT_SCORE, classify
from hub.notify.ports import NotifierPort
from hub.scraper.ports import ScraperPort, ScrapeResult
from hub.storage.groups import GroupRepository
from hub.storage.catalog import CatalogRepository
from hub.storage.posts import PostRepository
from hub.storage.runs import RunRepository

log = logging.getLogger(__name__)


class RunAlreadyActive(Exception):
    """A routine is already running."""


class RoutineRunner:
    """One routine: scrape -> dedup -> rules -> AI queue -> notify. Serial and throttled."""

    def __init__(
        self,
        groups: GroupRepository,
        posts: PostRepository,
        runs: RunRepository,
        scraper: ScraperPort,
        ai_queue: AiReviewQueue,
        notifier: NotifierPort,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        delay_fn: Callable[[], float] = lambda: random.uniform(2.0, 5.0),
        posts_per_group: int = 40,
        catalog: CatalogRepository | None = None,
    ) -> None:
        self._groups, self._posts, self._runs = groups, posts, runs
        self._scraper, self._ai_queue, self._notifier = scraper, ai_queue, notifier
        self._clock, self._sleep, self._delay_fn = clock, sleep, delay_fn
        self._posts_per_group = posts_per_group
        self._catalog = catalog
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._lock.locked()

    def run(self, trigger: Trigger) -> Run:
        if not self._lock.acquire(blocking=False):
            raise RunAlreadyActive()
        try:
            return self._execute(trigger)
        finally:
            self._lock.release()

    def _execute(self, trigger: Trigger) -> Run:
        run = self._runs.start(trigger, self._clock())
        groups = self._groups.list(only_enabled=True)
        errors: list[str] = []
        new_posts: list[Post] = []
        attention = False

        if groups:
            try:
                self._scraper.ensure_logged_in()
            except (LoginRequired, ChallengeDetected) as exc:
                attention = True
                errors.append(str(exc))
                self._notify_attention(exc)

        if groups and not attention:
            for index, group in enumerate(groups):
                if index > 0:
                    self._sleep(self._delay_fn())
                try:
                    result = self._scraper.fetch_posts(group.url, self._posts_per_group)
                except (LoginRequired, ChallengeDetected) as exc:
                    self._groups.record_run_result(group.id, self._clock(), "attention", 0)
                    run.groups_failed += 1
                    errors.append(str(exc))
                    attention = True
                    self._notify_attention(exc)
                    break
                except ScrapeError as exc:
                    self._fail_group(run, group, errors, str(exc) or type(exc).__name__)
                    continue
                except Exception as exc:  # contain anything unexpected to a single group
                    log.exception("Unexpected error scraping %s", group.url)
                    self._fail_group(run, group, errors, f"Unexpected: {exc}")
                    continue
                added = self._ingest(group, result)
                new_posts.extend(added)
                run.groups_ok += 1
                self._groups.record_run_result(
                    group.id, self._clock(), "ok", len(added), name=result.group_name, fb_group_id=result.group_id,
                )

        self._ai_queue.process()
        self._notify_new(new_posts)

        run.new_posts = len(new_posts)
        run.status = self._final_status(run, attention)
        run.error = "; ".join(errors)[:500] or None
        run.finished_at = self._clock()
        self._runs.finish(run)
        return run

    def _fail_group(self, run: Run, group: Group, errors: list[str], message: str) -> None:
        run.groups_failed += 1
        errors.append(message)
        self._groups.record_run_result(group.id, self._clock(), "error", 0)

    @staticmethod
    def _final_status(run: Run, attention: bool) -> RunStatus:
        if attention:
            return RunStatus.NEEDS_ATTENTION
        if run.groups_failed and not run.groups_ok:
            return RunStatus.FAILED
        if run.groups_failed:
            return RunStatus.PARTIAL
        return RunStatus.OK

    def _ingest(self, group: Group, result: ScrapeResult) -> list[Post]:
        unique: dict[str, ScrapedPost] = {p.post_id: p for p in result.posts}
        known = self._posts.known_ids(list(unique))
        added: list[Post] = []
        now = self._clock()
        for scraped in unique.values():
            if scraped.post_id in known:
                continue
            classification = classify(scraped.body)
            relevant = classification.kind != "other"
            post = Post(
                scraped=scraped, classification=classification, group_id=group.id,
                ai_status=AiStatus.PENDING if relevant else AiStatus.SKIPPED,
                first_seen_at=now, is_new=relevant,
            )
            self._posts.add(post)
            if relevant:
                added.append(post)
        if self._catalog and any(p.classification.kind == 'job' for p in added):
            self._catalog.sync_posts()
        return added

    def _notify_new(self, new_posts: list[Post]) -> None:
        relevant = [p for p in new_posts if p.classification.score >= RELEVANT_SCORE]
        if not relevant:
            return
        stays = sum(1 for p in relevant if p.classification.kind == "stay")
        jobs = len(relevant) - stays
        self._notifier.notify(
            "Sydney Hub",
            f"{len(relevant)} novos posts relevantes ({stays} quartos, {jobs} vagas)",
        )

    def _notify_attention(self, exc: Exception) -> None:
        if isinstance(exc, LoginRequired):
            message = "Login necessário no Facebook. A rotina foi pausada; entre no portal Facebook do Maestri."
        else:
            message = "O Facebook pediu verificação (checkpoint/captcha). Resolva manualmente no portal; a rotina foi pausada."
        self._notifier.notify("Sydney Hub — ação necessária", message)
