from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hub.api.app import AppDeps, create_app
from hub.domain.models import (
    AiResult, Classification, Post, Run, RunStatus, ScrapedPost, Trigger,
)
from hub.pipeline.routine import RunAlreadyActive
from hub.storage.db import Database
from hub.storage.catalog import CatalogRepository
from hub.storage.groups import GroupRepository
from hub.storage.posts import PostRepository
from hub.storage.runs import RunRepository
from datetime import datetime

GROUP = "https://www.facebook.com/groups/alpha/"
HOST = "127.0.0.1:8765"


class FakeRunner:
    def __init__(self):
        self.running = False
        self.calls = []

    @property
    def is_running(self):
        return self.running

    def run(self, trigger):
        if self.running:
            raise RunAlreadyActive()
        self.calls.append(trigger)
        return Run(trigger=trigger, started_at=1.0)


class FakeScheduler:
    def next_run_at(self):
        return datetime(2026, 12, 1, 14, 0)


@pytest.fixture
def ctx(tmp_path):
    db = Database(tmp_path / "hub.db")
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>dashboard</html>", encoding="utf-8")
    deps = AppDeps(
        groups=GroupRepository(db), posts=PostRepository(db), runs=RunRepository(db),
        catalog=CatalogRepository(db),
        runner=FakeRunner(), scheduler=FakeScheduler(), web_dir=web,
        allowed_hosts={"127.0.0.1:8765", "localhost:8765"}, spawn=lambda fn: fn(),
    )
    client = TestClient(create_app(deps), base_url=f"http://{HOST}")
    return type("Ctx", (), dict(client=client, deps=deps))


def add_post(ctx, pid, kind="stay", score=70, ts=1000.0, ai=None):
    sp = ScrapedPost(post_id=pid, group_url=GROUP, permalink=f"{GROUP}posts/{pid}/", body="Room Rosebery $300 pw",
                     created_ts=ts, author="Ana", group_name="Alpha", photos=["http://i/1.jpg"])
    post = Post(scraped=sp, classification=Classification(kind=kind, price_week=300, suburb="Rosebery", score=score),
                first_seen_at=ts)
    ctx.deps.posts.add(post)
    if ai:
        ctx.deps.posts.save_ai(pid, ai)


def test_built_dashboard_and_assets_serve_with_api(ctx, tmp_path):
    import shutil
    from web.build_dashboard import build

    source = Path(__file__).resolve().parents[1] / 'web'
    web = tmp_path / 'built-web'
    (web / 'seed').mkdir(parents=True)
    for name in ('template.html', 'atlas.css', 'zeus-engraving.webp'):
        shutil.copy2(source / name, web / name)
    shutil.copy2(source / 'seed' / 'suburb_centroids.json', web / 'seed' / 'suburb_centroids.json')
    build(web)
    client = TestClient(create_app(replace(ctx.deps, web_dir=web)), base_url=f"http://{HOST}")
    assert "Visualização de moradias" in client.get("/").text
    assert "zeus-engraving.webp" in client.get("/atlas.css").text
    image = client.get("/zeus-engraving.webp")
    assert image.status_code == 200 and image.headers["content-type"] == "image/webp"
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/posts?kind=stay").json() == []


def test_opportunities_api_filters_persisted_catalog_without_assumptions(ctx, tmp_path):
    seed = tmp_path / 'jobs.json'
    seed.write_text('[{"id":"one","title":"Barista","company":"Cafe","source":"Seek","url":"https://seek.example/job/1","suburb":"Rockdale","pay_display":"$40/h"},{"id":"two","title":"Cleaner","company":"Hotel","source":"Direct Employer","url":"https://hotel.example","suburb":"Mascot"}]')
    ctx.deps.catalog.import_legacy_seed(seed)
    response = ctx.client.get('/api/opportunities?role=Barista&suburb=Rockdale')
    assert response.status_code == 200
    assert [x['id'] for x in response.json()] == ['one']
    assert response.json()[0]['pay_display'] is None
    assert ctx.client.get('/api/opportunities?verification_state=employer_lead').json()[0]['id'] == 'two'
    assert ctx.client.get('/api/opportunities?limit=0').status_code == 422
    assert ctx.client.get('/api/opportunities?verification_state=fake').status_code == 422
    assert ctx.client.get('/api/opportunities?max_commute_minutes=30').status_code == 422
    assert ctx.client.get('/api/opportunities?origin_suburb=Kogarah&max_commute_minutes=30').json() == []


def test_opportunities_api_preserves_facebook_group_provenance(ctx):
    add_post(ctx, 'community-1', kind='job')
    ctx.deps.catalog.sync_posts()
    item = ctx.client.get('/api/opportunities').json()[0]
    assert item['id'] == 'community-1'
    assert item['source_group_name'] == 'Alpha'
    assert item['source_group_url'] == GROUP
    assert item['source_url'].endswith('/posts/community-1/')


# --- groups ------------------------------------------------------------------------------

def test_add_and_list_groups(ctx):
    r = ctx.client.post("/api/groups", json={"url": "facebook.com/groups/alpha?ref=x"})
    assert r.status_code == 201 and r.json()["url"] == GROUP and r.json()["enabled"] is True
    listing = ctx.client.get("/api/groups").json()
    assert [g["url"] for g in listing] == [GROUP]


def test_add_group_rejects_invalid_url_and_duplicates(ctx):
    assert ctx.client.post("/api/groups", json={"url": "https://example.com/x"}).status_code == 422
    assert ctx.client.post("/api/groups", json={"url": GROUP}).status_code == 201
    assert ctx.client.post("/api/groups", json={"url": GROUP}).status_code == 409


def test_toggle_and_delete_group(ctx):
    gid = ctx.client.post("/api/groups", json={"url": GROUP}).json()["id"]
    r = ctx.client.patch(f"/api/groups/{gid}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert ctx.client.delete(f"/api/groups/{gid}").status_code == 204
    assert ctx.client.delete(f"/api/groups/{gid}").status_code == 404
    assert ctx.client.patch(f"/api/groups/{gid}", json={"enabled": True}).status_code == 404


# --- runs & status -----------------------------------------------------------------------

def test_run_now_triggers_manual_run(ctx):
    r = ctx.client.post("/api/runs")
    assert r.status_code == 202
    assert ctx.deps.runner.calls == [Trigger.MANUAL]


def test_run_now_conflicts_when_already_running(ctx):
    ctx.deps.runner.running = True
    assert ctx.client.post("/api/runs").status_code == 409


def test_status_idle_with_next_run_and_counters(ctx):
    ctx.client.post("/api/groups", json={"url": GROUP})
    add_post(ctx, "a")
    s = ctx.client.get("/api/status").json()
    assert s["state"] == "idle" and s["next_run_at"].startswith("2026-12-01T14:00")
    assert s["new_posts"] == 1 and s["ai_pending"] == 1
    assert s["groups_total"] == 1 and s["groups_enabled"] == 1 and s["last_run"] is None


def test_status_reflects_running_and_attention(ctx):
    ctx.deps.runner.running = True
    assert ctx.client.get("/api/status").json()["state"] == "running"
    ctx.deps.runner.running = False
    run = ctx.deps.runs.start(Trigger.SCHEDULE, now=5.0)
    run.status, run.finished_at, run.error = RunStatus.NEEDS_ATTENTION, 6.0, "Facebook session is not logged in"
    ctx.deps.runs.finish(run)
    s = ctx.client.get("/api/status").json()
    assert s["state"] == "needs_attention" and "logged in" in s["last_run"]["error"]


# --- posts -------------------------------------------------------------------------------

def test_list_posts_by_kind_and_new(ctx):
    add_post(ctx, "s1", kind="stay", ts=100.0)
    add_post(ctx, "j1", kind="job", ts=200.0)
    assert [p["id"] for p in ctx.client.get("/api/posts").json()] == ["j1", "s1"]
    assert [p["id"] for p in ctx.client.get("/api/posts?kind=stay").json()] == ["s1"]
    ctx.client.post("/api/posts/seen")
    assert ctx.client.get("/api/posts?new=true").json() == []


def test_post_view_exposes_classification_and_ai(ctx):
    add_post(ctx, "s1", ai=AiResult(kind="stay", fit_score=90, summary="Ótimo", scam_suspected=False))
    p = ctx.client.get("/api/posts").json()[0]
    assert p["price_week"] == 300 and p["suburb"] == "Rosebery" and p["photos"] == ["http://i/1.jpg"]
    assert p["ai"]["fit_score"] == 90 and p["ai_status"] == "done" and p["is_new"] is True


def test_ai_marking_post_as_other_hides_it(ctx):
    add_post(ctx, "s1", ai=AiResult(kind="other"))
    add_post(ctx, "s2")
    assert [p["id"] for p in ctx.client.get("/api/posts").json()] == ["s2"]


def test_patch_post_user_status(ctx):
    add_post(ctx, "s1")
    assert ctx.client.patch("/api/posts/s1", json={"user_status": "fav"}).json()["user_status"] == "fav"
    assert ctx.client.patch("/api/posts/s1", json={"user_status": "bogus"}).status_code == 422
    assert ctx.client.patch("/api/posts/nope", json={"user_status": "fav"}).status_code == 404


# --- security & static -------------------------------------------------------------------

def test_dashboard_is_served_at_root(ctx):
    r = ctx.client.get("/")
    assert r.status_code == 200 and "dashboard" in r.text


def test_foreign_host_header_is_rejected(ctx):
    assert ctx.client.get("/api/groups", headers={"Host": "evil.example.com"}).status_code == 403


def test_cross_origin_mutation_is_rejected(ctx):
    r = ctx.client.post("/api/runs", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403 and ctx.deps.runner.calls == []


def test_same_origin_mutation_is_allowed(ctx):
    r = ctx.client.post("/api/runs", headers={"Origin": f"http://{HOST}"})
    assert r.status_code == 202


def test_tailored_cv_endpoint_allowlist(tmp_path, ctx):
    cv_dir = tmp_path / "output"
    cv_dir.mkdir()
    (cv_dir / "CV_Test.pdf").write_bytes(b"%PDF-1.4\n")
    (cv_dir / "secret.txt").write_text("nope", encoding="utf-8")
    ctx.deps.cv_dir = cv_dir
    app = create_app(ctx.deps)
    client = TestClient(app, base_url=f"http://{HOST}")
    assert client.get("/api/cv/CV_Test.pdf").status_code == 200
    assert client.get("/api/cv/secret.txt").status_code == 404
    assert client.get("/api/cv/..%2Fsecret.txt").status_code == 404
    assert client.get("/api/cv/missing.pdf").status_code == 404
