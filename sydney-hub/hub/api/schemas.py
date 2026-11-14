from __future__ import annotations

from pydantic import BaseModel, Field


class GroupIn(BaseModel):
    url: str = Field(min_length=3, max_length=300)


class GroupPatch(BaseModel):
    enabled: bool


class PostPatch(BaseModel):
    user_status: str = Field(pattern=r"^(|fav|contacted|applied|discarded)$")


class GroupOut(BaseModel):
    id: int
    url: str
    name: str | None
    enabled: bool
    last_run_at: float | None
    last_status: str | None
    last_new_posts: int
