from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator
from typing import Literal


class ScrapeSettings(BaseModel):
    portal: str = "Facebook"
    script_path: str
    posts_per_group: int = Field(40, ge=5, le=200)
    delay_seconds: tuple[float, float] = (2.0, 5.0)


class AiSettings(BaseModel):
    agent: str = "Pergaminho"
    batch_size: int = Field(15, ge=1, le=40)


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8765

    @field_validator("host")
    @classmethod
    def local_only(cls, value: str) -> str:
        if value not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("The hub must only listen on localhost")
        return value


class PathSettings(BaseModel):
    db: str = "data/hub.db"
    web: str = "web"


class EmployerDiscoverySettings(BaseModel):
    enabled: bool = False
    provider: Literal['osm_overpass'] = 'osm_overpass'
    max_requests: int = Field(80, ge=1, le=80)


class Settings(BaseModel):
    schedule: list[str]
    scrape: ScrapeSettings
    ai: AiSettings = AiSettings()
    server: ServerSettings = ServerSettings()
    paths: PathSettings = PathSettings()
    employer_discovery: EmployerDiscoverySettings = EmployerDiscoverySettings()
    seed_groups: list[str] = []
    root: Path = Path(".")

    def resolve(self, relative: str) -> Path:
        return (self.root / relative).resolve()


def load_settings(path: Path) -> Settings:
    if not path.exists():
        path = path.with_name("settings.example.yml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Settings(**data, root=path.parent.parent)
