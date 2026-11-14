from __future__ import annotations

from typing import Protocol

from hub.domain.models import AiResult, Post


class AiClassifierPort(Protocol):
    def is_available(self) -> bool:
        """True when the AI agent can be reached right now."""

    def enrich(self, posts: list[Post]) -> dict[str, AiResult]:
        """Return an AiResult per post_id for the posts the AI managed to review."""
