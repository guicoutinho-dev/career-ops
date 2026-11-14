from __future__ import annotations

import json
import sys
import time
from typing import Callable

from hub.adapters.process import CommandResult, CommandRunner, subprocess_runner
from hub.domain.models import (
    ChallengeDetected, GroupUnavailable, LoginRequired, ScrapedPost, ScrapeSchemaChanged,
)
from hub.scraper.ports import ScrapeResult

PROBE_JS = (
    "JSON.stringify({url: location.href, "
    "user_id: (document.cookie.match(/c_user=(\\d+)/) || [])[1] || '0', "
    "challenge: /checkpoint|captcha|two_step_verification|security_check/i.test(location.href) "
    "|| !!document.querySelector('iframe[src*=\"captcha\"], iframe[src*=\"recaptcha\"]')})"
)
POLL_JS = "JSON.stringify({done: window.__fbScrapeState?.done, error: window.__fbScrapeState?.error})"
RESULT_JS = "window.__fbScrapeState?.result || '{}'"

_SCHEMA_MARKERS = ("persistedquerynotfound", "doc_id", "persisted query")
_SESSION_MARKERS = ("field_exception", "missing_required_variable_value", "login")


class MaestriPortalScraper:
    """Scrapes a group through the user's logged-in Maestri 'Facebook' portal. Read-only."""

    def __init__(
        self,
        maestri: str,
        script_path: str,
        portal: str = "Facebook",
        runner: CommandRunner = subprocess_runner,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval: float = 2.0,
        max_polls: int = 90,
        page_settle_seconds: float = 4.0,
    ) -> None:
        self._maestri, self._script, self._portal = maestri, script_path, portal
        self._run, self._sleep = runner, sleep
        self._poll_interval, self._max_polls, self._settle = poll_interval, max_polls, page_settle_seconds

    # -- ScraperPort -------------------------------------------------------------------------

    def ensure_logged_in(self) -> None:
        self._navigate("https://www.facebook.com/")
        self._check_session()

    def fetch_posts(self, group_url: str, count: int) -> ScrapeResult:
        self._navigate(group_url)
        self._check_session()
        iife = self._build_scrape_js(count)
        self._evaluate(
            "(() => {window.__fbScrapeState = {done:false,result:null,error:null};"
            f"({iife}).then(r => {{window.__fbScrapeState.result = r; window.__fbScrapeState.done = true;}})"
            ".catch(e => {window.__fbScrapeState.error = String(e); window.__fbScrapeState.done = true;});"
            "return 'STARTED';})()"
        )
        self._wait_until_done()
        payload = self._parse_json(self._evaluate(RESULT_JS))
        return self._to_result(payload)

    # -- helpers -----------------------------------------------------------------------------

    def _navigate(self, url: str) -> None:
        result = self._run([self._maestri, "portal", "navigate", self._portal, url], timeout=60)
        if result.returncode != 0:
            raise GroupUnavailable(f"Could not open {url} in portal '{self._portal}': {result.stderr.strip()}")
        self._sleep(self._settle)

    def _evaluate(self, js: str) -> str:
        result = self._run([self._maestri, "portal", "evaluate", self._portal, js], timeout=120)
        if result.returncode != 0:
            raise GroupUnavailable(f"Portal evaluate failed: {result.stderr.strip() or result.stdout.strip()}")
        return result.stdout.strip()

    @staticmethod
    def _parse_json(raw: str) -> dict:
        try:
            data = json.loads(raw)
            if isinstance(data, str):
                data = json.loads(data)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ScrapeSchemaChanged(f"Unreadable scraper output: {raw[:120]!r}") from exc
        return data if isinstance(data, dict) else {}

    def _check_session(self) -> None:
        state = self._parse_json(self._evaluate(PROBE_JS))
        if state.get("challenge"):
            raise ChallengeDetected("Facebook showed a checkpoint or CAPTCHA")
        url = str(state.get("url", ""))
        if "/login" in url or state.get("user_id", "0") in ("0", "", None):
            raise LoginRequired("Facebook session is not logged in")

    def _build_scrape_js(self, count: int) -> str:
        result = self._run(
            [sys.executable, self._script, "--sort", "CHRONOLOGICAL", "--count", str(count),
             "--max-pages", str(max(2, count // 3 + 2))],
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise ScrapeSchemaChanged(f"Scrape script failed: {result.stderr.strip()[:200]}")
        return result.stdout.strip()

    def _wait_until_done(self) -> None:
        for _ in range(self._max_polls):
            self._sleep(self._poll_interval)
            state = self._parse_json(self._evaluate(POLL_JS))
            if state.get("done"):
                if state.get("error"):
                    raise ScrapeSchemaChanged(str(state["error"]))
                return
        raise GroupUnavailable("Timed out waiting for the scrape to finish")

    def _to_result(self, payload: dict) -> ScrapeResult:
        pages = (payload.get("diagnostics") or {}).get("pages") or []
        errors = [str(p.get("err")) for p in pages if p.get("err")]
        if payload.get("error"):
            errors.append(str(payload["error"]))
        joined = " | ".join(errors).lower()

        posts = [self._to_post(p) for p in payload.get("posts") or [] if p.get("post_id")]
        if not posts and errors:
            if any(m in joined for m in _SCHEMA_MARKERS):
                raise ScrapeSchemaChanged(errors[0])
            if any(m in joined for m in _SESSION_MARKERS):
                raise LoginRequired(errors[0])
            raise GroupUnavailable(errors[0])
        return ScrapeResult(group_id=payload.get("group_id"), group_name=payload.get("group_name"), posts=posts)

    @staticmethod
    def _to_post(raw: dict) -> ScrapedPost:
        author = raw.get("author") or {}
        group = raw.get("group") or {}
        photos = [
            m.get("photo_image") or m.get("thumbnail") for m in raw.get("media") or []
            if m.get("photo_image") or m.get("thumbnail")
        ]
        return ScrapedPost(
            post_id=str(raw["post_id"]),
            group_url=group.get("url") or "",
            permalink=raw.get("permalink_url") or "",
            body=(raw.get("message") or "").strip(),
            created_ts=float(raw.get("creation_time") or 0),
            author=author.get("name") or "",
            author_url=author.get("url") or "",
            group_name=group.get("name") or "",
            photos=photos,
            comment_count=int(raw.get("comment_count") or 0),
            reactions=int((raw.get("reactions") or {}).get("total") or 0),
        )
