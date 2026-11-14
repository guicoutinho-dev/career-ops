from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from hub.classify.ports import AiClassifierPort
from hub.classify.queue import AiReviewQueue
from hub.domain.models import (
    AiResult, ChallengeDetected, GroupUnavailable, LoginRequired, RunStatus,
    ScrapedPost, ScrapeSchemaChanged, Trigger,
)
from hub.pipeline.routine import RoutineRunner, RunAlreadyActive
from hub.scraper.ports import ScrapeResult
from hub.storage.db import Database
from hub.storage.catalog import CatalogRepository
from hub.storage.groups import GroupRepository
from hub.storage.posts import PostRepository
from hub.storage.runs import RunRepository

G1 = "https://www.facebook.com/groups/alpha/"
G2 = "https://www.facebook.com/groups/beta/"

GOOD_STAY = "Private room in Rosebery, $300 per week, bills included, ensuite"
GOOD_JOB = "Hiring cleaner for construction site, $36/hr, White Card, call 0491 617 364"
NOISE = "Alguém sabe onde comprar chip Telstra?"


def post(pid, body, group=G1, ts=1000.0):
    return ScrapedPost(post_id=pid, group_url=group, permalink=f"{group}posts/{pid}/", body=body, created_ts=ts, author="A")


@dataclass
class FakeScraper:
    results: dict = field(default_factory=dict)  # url -> ScrapeResult | Exception
    login_error: Exception | None = None
    fetched: list = field(default_factory=list)

    def ensure_logged_in(self):
        if self.login_error:
            raise self.login_error

    def fetch_posts(self, group_url, count):
        self.fetched.append(group_url)
        outcome = self.results[group_url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def notify(self, title, message):
        self.sent.append((title, message))


class FakeAi:
    def __init__(self, available=True):
        self.available = available
        self.calls = []

    def is_available(self):
        return self.available

    def enrich(self, posts):
        self.calls.append([p.scraped.post_id for p in posts])
        return {p.scraped.post_id: AiResult(kind=p.classification.kind, fit_score=80, summary="ok") for p in posts}


@pytest.fixture
def env(tmp_path):
    db = Database(tmp_path / "hub.db")
    groups, posts, runs = GroupRepository(db), PostRepository(db), RunRepository(db)
    catalog = CatalogRepository(db)
    scraper, notifier, ai = FakeScraper(), FakeNotifier(), FakeAi()
    sleeps = []
    clock = iter(range(1000, 2000))
    runner = RoutineRunner(
        groups=groups, posts=posts, runs=runs, scraper=scraper,
        ai_queue=AiReviewQueue(posts, ai), notifier=notifier,
        clock=lambda: float(next(clock)), sleep=sleeps.append, delay_fn=lambda: 3.0,
        catalog=catalog,
    )
    return type("Env", (), dict(groups=groups, posts=posts, runs=runs, catalog=catalog, scraper=scraper,
                                notifier=notifier, ai=ai, runner=runner, sleeps=sleeps))


def test_happy_path_stores_new_posts_and_notifies(env):
    env.groups.add(G1, now=1.0)
    env.scraper.results[G1] = ScrapeResult("111", "Alpha", [post("a", GOOD_STAY), post("b", GOOD_JOB), post("c", NOISE)])
    run = env.runner.run(Trigger.MANUAL)
    assert run.status == RunStatus.OK
    assert (run.groups_ok, run.groups_failed, run.new_posts) == (1, 0, 2)
    assert {p.scraped.post_id for p in env.posts.list()} == {"a", "b"}
    group = env.groups.list()[0]
    assert (group.name, group.fb_group_id, group.last_status, group.last_new_posts) == ("Alpha", "111", "ok", 2)
    assert len(env.notifier.sent) == 1
    assert "2" in env.notifier.sent[0][1]
    jobs = env.catalog.list()
    assert [j['id'] for j in jobs] == ['b']
    assert jobs[0]['verification_state'] == 'unverified_job_post'
    assert jobs[0]['pay_display'] == '$36/hr'
    assert jobs[0]['employment_type'] is None
    assert '$36/hr' in (jobs[0]['evidence'][0]['excerpt'] or '')


def test_second_run_deduplicates_including_noise(env):
    env.groups.add(G1, now=1.0)
    env.scraper.results[G1] = ScrapeResult("111", "Alpha", [post("a", GOOD_STAY), post("c", NOISE)])
    env.runner.run(Trigger.MANUAL)
    env.notifier.sent.clear()
    again = env.runner.run(Trigger.SCHEDULE)
    assert again.new_posts == 0
    assert env.notifier.sent == []


def test_no_notification_when_new_posts_are_not_relevant(env):
    env.groups.add(G1, now=1.0)
    env.scraper.results[G1] = ScrapeResult("1", "Alpha", [post("x", "Room in Bondi $900 per week")])
    run = env.runner.run(Trigger.MANUAL)
    assert run.new_posts == 1
    assert env.notifier.sent == []


def test_group_failure_does_not_abort_run(env):
    env.groups.add(G1, now=1.0)
    env.groups.add(G2, now=1.0)
    env.scraper.results[G1] = GroupUnavailable("private")
    env.scraper.results[G2] = ScrapeResult("2", "Beta", [post("b", GOOD_STAY, G2)])
    run = env.runner.run(Trigger.MANUAL)
    assert run.status == RunStatus.PARTIAL
    assert (run.groups_ok, run.groups_failed) == (1, 1)
    by_url = {g.url: g for g in env.groups.list()}
    assert by_url[G1].last_status == "error"
    assert by_url[G2].last_status == "ok"


def test_all_groups_failing_marks_run_failed(env):
    env.groups.add(G1, now=1.0)
    env.scraper.results[G1] = ScrapeSchemaChanged("doc_id rotated")
    run = env.runner.run(Trigger.MANUAL)
    assert run.status == RunStatus.FAILED
    assert "doc_id" in run.error


def test_unexpected_error_in_one_group_is_contained(env):
    env.groups.add(G1, now=1.0)
    env.groups.add(G2, now=1.0)
    env.scraper.results[G1] = RuntimeError("boom")
    env.scraper.results[G2] = ScrapeResult("2", "Beta", [])
    assert env.runner.run(Trigger.MANUAL).status == RunStatus.PARTIAL


def test_login_required_pauses_run_and_notifies(env):
    env.groups.add(G1, now=1.0)
    env.scraper.login_error = LoginRequired("not logged in")
    run = env.runner.run(Trigger.SCHEDULE)
    assert run.status == RunStatus.NEEDS_ATTENTION
    assert env.scraper.fetched == []
    assert len(env.notifier.sent) == 1
    assert "login" in env.notifier.sent[0][1].lower()


def test_challenge_mid_run_stops_immediately_and_keeps_earlier_posts(env):
    env.groups.add(G1, now=1.0)
    env.groups.add(G2, now=1.0)
    env.scraper.results[G1] = ScrapeResult("1", "Alpha", [post("a", GOOD_STAY)])
    env.scraper.results[G2] = ChallengeDetected("checkpoint")
    run = env.runner.run(Trigger.MANUAL)
    assert run.status == RunStatus.NEEDS_ATTENTION
    assert env.posts.get("a") is not None
    assert env.scraper.fetched == [G1, G2]


def test_challenge_does_not_continue_to_next_group(env):
    env.groups.add(G1, now=1.0)
    env.groups.add(G2, now=1.0)
    env.scraper.results[G1] = ChallengeDetected("captcha")
    env.scraper.results[G2] = ScrapeResult("2", "Beta", [])
    env.runner.run(Trigger.MANUAL)
    assert env.scraper.fetched == [G1]


def test_disabled_groups_are_skipped(env):
    g = env.groups.add(G1, now=1.0)
    env.groups.set_enabled(g.id, False)
    run = env.runner.run(Trigger.MANUAL)
    assert run.status == RunStatus.OK and env.scraper.fetched == []


def test_groups_are_throttled_serially(env):
    env.groups.add(G1, now=1.0)
    env.groups.add(G2, now=1.0)
    env.scraper.results[G1] = ScrapeResult("1", "A", [])
    env.scraper.results[G2] = ScrapeResult("2", "B", [])
    env.runner.run(Trigger.MANUAL)
    assert env.sleeps == [3.0]


def test_ai_enrichment_runs_when_available(env):
    env.groups.add(G1, now=1.0)
    env.scraper.results[G1] = ScrapeResult("1", "A", [post("a", GOOD_STAY)])
    env.runner.run(Trigger.MANUAL)
    assert env.ai.calls == [["a"]]
    assert env.posts.count_pending_ai() == 0


def test_ai_unavailable_leaves_posts_pending_without_failing(env):
    env.ai.available = False
    env.groups.add(G1, now=1.0)
    env.scraper.results[G1] = ScrapeResult("1", "A", [post("a", GOOD_STAY)])
    run = env.runner.run(Trigger.MANUAL)
    assert run.status == RunStatus.OK
    assert env.posts.count_pending_ai() == 1


def test_overlapping_runs_are_rejected(env):
    assert env.runner._lock.acquire(blocking=False)
    try:
        with pytest.raises(RunAlreadyActive):
            env.runner.run(Trigger.MANUAL)
    finally:
        env.runner._lock.release()
    assert env.runner.is_running is False


def test_ai_queue_processes_only_batch_size_and_ignores_missing_results(env):
    env.groups.add(G1, now=1.0)
    env.scraper.results[G1] = ScrapeResult("1", "A", [post(str(i), GOOD_STAY) for i in range(5)])
    env.ai.available = False
    env.runner.run(Trigger.MANUAL)

    class PartialAi:
        def is_available(self): return True
        def enrich(self, posts): return {posts[0].scraped.post_id: AiResult(kind="stay", fit_score=50)}

    queue = AiReviewQueue(env.posts, PartialAi(), batch_size=3)
    assert queue.process() == 1
    assert env.posts.count_pending_ai() == 4
