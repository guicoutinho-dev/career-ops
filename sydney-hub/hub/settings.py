from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


_FORBIDDEN_CONFIG_PREFIXES = (
    os.path.normcase(os.path.normpath(r"D:\AntigravityProjetos\career-ops")),
    os.path.normcase(os.path.normpath(r"C:\Users\guicx\.codex\worktrees")),
)


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
    provider: Literal['osm_overpass', 'osm_pbf'] = 'osm_overpass'
    pbf_path: str = 'data/osm/new-south-wales-latest.osm.pbf'
    max_requests: int = Field(80, ge=1, le=2000)


class PublishedJobsSettings(BaseModel):
    """Configuration for the isolated Facebook-free Published Jobs runtime."""

    db_path: str = "data/jobs_isolated/jobs_hub.db"
    isolated_db_dir: str = "data/jobs_isolated"
    policies_path: str = "config/source_policies.yml"
    boards_path: str = "config/published_boards.yml"
    state_dir: str = "data/published-state"
    reports_dir: str = "data/pilot-reports"
    max_verifications: int = Field(30, ge=1, le=200)
    workable_enabled: bool = True
    workable_cooldown_seconds: int = Field(3600, ge=60, le=86400)
    workable_min_interval_hours: int = Field(20, ge=1, le=168)
    geocode_street_addresses: bool = False
    origin_label: str = "Rockdale Station"
    scheduler_enabled: bool = False
    schedule: list[str] = Field(default_factory=list)
    adzuna_evaluate_only: bool = True


class JobsConfig(BaseModel):
    """Facebook-free configuration root for the isolated Jobs service and runner."""

    server: ServerSettings = ServerSettings(host="127.0.0.1", port=8796)
    paths: PathSettings = PathSettings(db="data/jobs_isolated/jobs_hub.db", web="web")
    published_jobs: PublishedJobsSettings = PublishedJobsSettings()
    root: Path = Path(".")

    @staticmethod
    def _reject_unsafe_relative(raw: str, root: Path, *, label: str) -> Path:
        raw_str = str(raw).strip()
        if not raw_str:
            raise ValueError(f"{label} cannot be empty")
        rel = Path(raw_str)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"{label} must be a relative path without '..' traversal: {raw}")
        root_real = Path(os.path.realpath(str(root)))
        norm_root = os.path.normcase(str(root_real))
        if any(norm_root == prefix or norm_root.startswith(prefix + os.sep) for prefix in _FORBIDDEN_CONFIG_PREFIXES):
            raise ValueError(f"Refusing protected V4/main checkout root: {root}")
        cursor = root
        for part in rel.parts:
            cursor = cursor / part
            if os.path.islink(cursor):
                raise ValueError(f"{label} contains symlink component: {cursor}")
        resolved = Path(os.path.realpath(str(root / rel)))
        if not resolved.is_relative_to(root_real):
            raise ValueError(f"{label} escapes config root {root_real}: {resolved}")
        return resolved

    @model_validator(mode="after")
    def _anchor_isolated_paths(self) -> "JobsConfig":
        root_real = Path(os.path.realpath(str(self.root)))
        anchor_dir = root_real / "data" / "jobs_isolated"
        isolated_dir = self._reject_unsafe_relative(
            self.published_jobs.isolated_db_dir,
            self.root,
            label="published_jobs.isolated_db_dir",
        )
        if isolated_dir != anchor_dir:
            raise ValueError(
                f"published_jobs.isolated_db_dir must resolve strictly to {anchor_dir}, got {isolated_dir}"
            )
        for label, raw_db in (
            ("published_jobs.db_path", self.published_jobs.db_path),
            ("paths.db", self.paths.db),
        ):
            resolved_db = self._reject_unsafe_relative(raw_db, self.root, label=label)
            if resolved_db == anchor_dir or not resolved_db.is_relative_to(anchor_dir):
                raise ValueError(f"{label} must stay inside {anchor_dir}, got {resolved_db}")
            if resolved_db.name.lower() == "hub.db":
                raise ValueError(f"{label} cannot use shared filename hub.db: {raw_db}")
        for label, rel_val in (
            ("paths.web", self.paths.web),
            ("published_jobs.policies_path", self.published_jobs.policies_path),
            ("published_jobs.boards_path", self.published_jobs.boards_path),
            ("published_jobs.state_dir", self.published_jobs.state_dir),
            ("published_jobs.reports_dir", self.published_jobs.reports_dir),
        ):
            self._reject_unsafe_relative(rel_val, self.root, label=label)
        return self

    def resolve(self, relative: str | Path) -> Path:
        return self._reject_unsafe_relative(str(relative), self.root, label="path")


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


def load_jobs_settings(path: Path) -> JobsConfig:
    """Load isolated Jobs configuration without requiring any Facebook settings."""
    if not path.exists():
        path = path.with_name("jobs.example.yml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return JobsConfig(**data, root=path.parent.parent)
