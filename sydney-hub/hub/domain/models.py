"""Pure domain models. No I/O and no imports from other hub modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RunStatus(str, Enum):
    RUNNING = "running"
    OK = "ok"
    PARTIAL = "partial"
    NEEDS_ATTENTION = "needs_attention"
    FAILED = "failed"


class Trigger(str, Enum):
    SCHEDULE = "schedule"
    MANUAL = "manual"
    CATCHUP = "catchup"


class AiStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    SKIPPED = "skipped"


class ScrapeError(Exception):
    """Base class for scraper failures."""


class LoginRequired(ScrapeError):
    """Facebook session is not logged in."""


class ChallengeDetected(ScrapeError):
    """Facebook showed a checkpoint, CAPTCHA or similar challenge. Never bypassed."""


class ScrapeSchemaChanged(ScrapeError):
    """Facebook's persisted query or response shape changed."""


class GroupUnavailable(ScrapeError):
    """Group cannot be read (private, removed or not a member)."""


@dataclass
class Group:
    url: str
    id: int | None = None
    fb_group_id: str | None = None
    name: str | None = None
    enabled: bool = True
    last_run_at: float | None = None
    last_status: str | None = None
    last_new_posts: int = 0
    created_at: float | None = None


@dataclass
class ScrapedPost:
    post_id: str
    group_url: str
    permalink: str
    body: str
    created_ts: float
    author: str = ""
    author_url: str = ""
    group_name: str = ""
    photos: list[str] = field(default_factory=list)
    comment_count: int = 0
    reactions: int = 0


@dataclass
class Classification:
    kind: str = "other"  # stay | job | other
    price_week: int | None = None
    pay_hourly: int | None = None
    suburb: str | None = None
    ticket: str | None = None
    phone: str | None = None
    bills_included: bool = False
    has_ensuite: bool = False
    scam_flags: list[str] = field(default_factory=list)
    score: int = 0


@dataclass
class AiResult:
    kind: str | None = None
    fit_score: int | None = None
    scam_suspected: bool = False
    summary: str = ""
    extracted: dict = field(default_factory=dict)


@dataclass
class Post:
    scraped: ScrapedPost
    classification: Classification
    group_id: int | None = None
    ai_status: AiStatus = AiStatus.PENDING
    ai: AiResult | None = None
    first_seen_at: float = 0.0
    is_new: bool = True
    user_status: str = ""


@dataclass
class Run:
    trigger: Trigger
    started_at: float
    id: int | None = None
    finished_at: float | None = None
    status: RunStatus = RunStatus.RUNNING
    groups_ok: int = 0
    groups_failed: int = 0
    new_posts: int = 0
    error: str | None = None
