from __future__ import annotations

from typing import Protocol


class NotifierPort(Protocol):
    def notify(self, title: str, message: str) -> None:
        """Show a desktop notification. Must never raise."""
