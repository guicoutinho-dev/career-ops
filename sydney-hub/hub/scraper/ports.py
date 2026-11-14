from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from hub.domain.models import ScrapedPost


@dataclass
class ScrapeResult:
    group_id: str | None
    group_name: str | None
    posts: list[ScrapedPost] = field(default_factory=list)


class ScraperPort(Protocol):
    def ensure_logged_in(self) -> None:
        """Raise LoginRequired or ChallengeDetected when the session cannot be used."""

    def fetch_posts(self, group_url: str, count: int) -> ScrapeResult:
        """Read the newest posts of a group, newest first."""
