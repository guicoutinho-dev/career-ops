from __future__ import annotations

import logging

from hub.classify.ports import AiClassifierPort
from hub.storage.posts import PostRepository

log = logging.getLogger(__name__)


class AiReviewQueue:
    """Sends pending posts to the AI agent in small batches, when the agent is reachable."""

    def __init__(self, posts: PostRepository, ai: AiClassifierPort, batch_size: int = 15) -> None:
        self._posts = posts
        self._ai = ai
        self._batch_size = batch_size

    def process(self) -> int:
        try:
            if not self._ai.is_available():
                return 0
            batch = self._posts.pending_ai(limit=self._batch_size)
            if not batch:
                return 0
            results = self._ai.enrich(batch)
        except Exception:  # the AI is best effort and must never break a routine
            log.exception("AI enrichment failed; posts stay pending")
            return 0
        saved = 0
        for post in batch:
            result = results.get(post.scraped.post_id)
            if result is not None:
                self._posts.save_ai(post.scraped.post_id, result)
                saved += 1
        return saved
