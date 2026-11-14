from __future__ import annotations

import json

import pytest

from hub.adapters.maestri_agent import MaestriAgentClassifier
from hub.adapters.maestri_portal import MaestriPortalScraper
from hub.adapters.process import CommandResult
from hub.adapters.windows_toast import WindowsToastNotifier
from hub.domain.models import (
    ChallengeDetected, Classification, GroupUnavailable, LoginRequired, Post, ScrapedPost,
    ScrapeSchemaChanged,
)

GROUP = "https://www.facebook.com/groups/alpha/"


class Script:
    """Fake command runner: maps a predicate on the command to a scripted result."""

    def __init__(self):
        self.calls = []
        self.probe = {"url": "https://www.facebook.com/", "user_id": "61560", "challenge": False}
        self.result = {"ok": True, "group_id": "111", "group_name": "Alpha", "posts": []}
        self.scrape_exit = 0

    def __call__(self, args, timeout=0, env=None):
        self.calls.append((args, env))
        if "navigate" in args:
            return CommandResult(0, "ok")
        if args[0].endswith("python.exe") or args[0].endswith("python"):
            return CommandResult(self.scrape_exit, "(async()=>({}))()", "" if not self.scrape_exit else "boom")
        if "evaluate" in args:
            js = args[-1]
            if "c_user" in js:
                return CommandResult(0, json.dumps(json.dumps(self.probe)))
            if "STARTED" in js:
                return CommandResult(0, "STARTED")
            if "done:" in js:
                return CommandResult(0, json.dumps(json.dumps({"done": True, "error": None})))
            return CommandResult(0, json.dumps(json.dumps(self.result)))
        return CommandResult(1, "", "unexpected")


def scraper(script):
    return MaestriPortalScraper(
        maestri="maestri", script_path="scrape-posts.py", runner=script,
        sleep=lambda _s: None, page_settle_seconds=0,
    )


def raw_post(pid="1", msg="Room $300 pw"):
    return {
        "post_id": pid, "permalink_url": f"{GROUP}posts/{pid}/", "creation_time": 1700000000,
        "message": msg, "author": {"name": "Ana", "url": "https://fb.com/ana"},
        "group": {"id": "111", "name": "Alpha", "url": GROUP}, "comment_count": 3,
        "reactions": {"total": 7}, "media": [{"photo_image": "http://img/1.jpg"}, {"thumbnail": "http://img/2.jpg"}],
    }


def test_login_check_passes_when_user_id_present():
    scraper(Script()).ensure_logged_in()


def test_login_required_when_user_id_missing():
    s = Script()
    s.probe["user_id"] = "0"
    with pytest.raises(LoginRequired):
        scraper(s).ensure_logged_in()


def test_login_required_when_redirected_to_login_page():
    s = Script()
    s.probe["url"] = "https://www.facebook.com/login/?next=x"
    with pytest.raises(LoginRequired):
        scraper(s).ensure_logged_in()


def test_challenge_is_detected_and_never_bypassed():
    s = Script()
    s.probe.update(challenge=True, url="https://www.facebook.com/checkpoint/1")
    with pytest.raises(ChallengeDetected):
        scraper(s).ensure_logged_in()


def test_fetch_posts_maps_fields():
    s = Script()
    s.result["posts"] = [raw_post("1"), raw_post("2")]
    result = scraper(s).fetch_posts(GROUP, 20)
    assert (result.group_id, result.group_name) == ("111", "Alpha")
    first = result.posts[0]
    assert first.post_id == "1" and first.author == "Ana" and first.reactions == 7
    assert first.photos == ["http://img/1.jpg", "http://img/2.jpg"]
    assert first.created_ts == 1700000000.0


def test_fetch_posts_empty_group_is_not_an_error():
    assert scraper(Script()).fetch_posts(GROUP, 20).posts == []


def test_rotated_doc_id_raises_schema_changed():
    s = Script()
    s.result = {"ok": False, "posts": [], "diagnostics": {"pages": [{"err": "PersistedQueryNotFound"}]}}
    with pytest.raises(ScrapeSchemaChanged):
        scraper(s).fetch_posts(GROUP, 20)


def test_field_exception_means_session_lost():
    s = Script()
    s.result = {"ok": False, "posts": [], "diagnostics": {"pages": [{"err": "field_exception"}]}}
    with pytest.raises(LoginRequired):
        scraper(s).fetch_posts(GROUP, 20)


def test_unresolvable_group_is_unavailable():
    s = Script()
    s.result = {"ok": False, "error": "Could not resolve numeric group_id from current page"}
    with pytest.raises(GroupUnavailable):
        scraper(s).fetch_posts(GROUP, 20)


def test_login_probe_runs_before_scraping_a_group():
    s = Script()
    s.probe["user_id"] = "0"
    with pytest.raises(LoginRequired):
        scraper(s).fetch_posts(GROUP, 20)
    assert not any("STARTED" in c[0][-1] for c in s.calls if "evaluate" in c[0])


def test_failing_scrape_script_raises_schema_changed():
    s = Script()
    s.scrape_exit = 2
    with pytest.raises(ScrapeSchemaChanged):
        scraper(s).fetch_posts(GROUP, 20)


# --- AI adapter ---------------------------------------------------------------------------

def stay_post(pid="p1"):
    sp = ScrapedPost(post_id=pid, group_url=GROUP, permalink="x", body="Room in Rosebery $300 pw", created_ts=1.0)
    return Post(scraped=sp, classification=Classification(kind="stay", score=70))


def ai_runner(list_out="", ask_out="", ask_code=0, list_code=0):
    def run(args, timeout=0, env=None):
        if "list" in args:
            return CommandResult(list_code, list_out)
        return CommandResult(ask_code, ask_out, "err")
    return run


def test_agent_available_when_listed():
    ai = MaestriAgentClassifier("maestri", "Pergaminho", runner=ai_runner('  - name: "Pergaminho", role: "x"'))
    assert ai.is_available() is True


def test_agent_unavailable_when_not_listed_or_cli_fails():
    assert MaestriAgentClassifier("maestri", "Pergaminho", runner=ai_runner('- name: "Farol"')).is_available() is False
    assert MaestriAgentClassifier("maestri", "Pergaminho", runner=ai_runner("", list_code=1)).is_available() is False


def test_enrich_parses_json_array_inside_prose():
    reply = 'Claro!\n[{"post_id": "p1", "kind": "stay", "fit_score": 130, "scam_suspected": false, "summary": "Bom", "extracted": {"price": "$300"}}]\nPronto.'
    ai = MaestriAgentClassifier("maestri", runner=ai_runner(ask_out=reply))
    out = ai.enrich([stay_post("p1")])
    assert out["p1"].kind == "stay" and out["p1"].fit_score == 100 and out["p1"].extracted["price"] == "$300"


def test_enrich_ignores_unknown_ids_invalid_kind_and_garbage():
    reply = '[{"post_id": "zzz", "kind": "stay"}, {"post_id": "p1", "kind": "weird", "fit_score": "high"}]'
    out = MaestriAgentClassifier("maestri", runner=ai_runner(ask_out=reply)).enrich([stay_post("p1")])
    assert "zzz" not in out and out["p1"].kind is None and out["p1"].fit_score is None
    assert MaestriAgentClassifier("maestri", runner=ai_runner(ask_out="no json here")).enrich([stay_post()]) == {}
    assert MaestriAgentClassifier("maestri", runner=ai_runner(ask_out="[bad json")).enrich([stay_post()]) == {}


def test_enrich_returns_empty_when_ask_fails():
    assert MaestriAgentClassifier("maestri", runner=ai_runner(ask_code=1)).enrich([stay_post()]) == {}


def test_prompt_marks_posts_as_untrusted_data():
    captured = {}

    def run(args, timeout=0, env=None):
        captured["prompt"] = args[-1]
        return CommandResult(0, "[]")

    MaestriAgentClassifier("maestri", runner=run).enrich([stay_post("p9")])
    assert "untrusted DATA" in captured["prompt"] and "post_id: p9" in captured["prompt"]


# --- Toast --------------------------------------------------------------------------------

def test_toast_passes_text_through_env_and_never_raises():
    seen = {}

    def run(args, timeout=0, env=None):
        seen["env"] = env
        return CommandResult(0, "")

    WindowsToastNotifier(runner=run).notify("Title", "It's \"quoted\" $(whoami)")
    assert seen["env"]["HUB_TOAST_MESSAGE"] == "It's \"quoted\" $(whoami)"

    def boom(*a, **k):
        raise RuntimeError("x")

    WindowsToastNotifier(runner=boom).notify("t", "m")  # must not raise
