"""Read-only, bounded subprocess bridge to Career Ops' providers/*.mjs ATS adapters."""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_BRIDGE_JS = Path(__file__).resolve().with_name("ats_bridge.mjs")
_SAFE_ENV_KEYS = ("PATH", "PATHEXT", "SYSTEMROOT", "SystemRoot", "COMSPEC", "ComSpec", "TMP", "TEMP", "HOME", "USERPROFILE", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS")


@dataclass(frozen=True)
class AtsMatch:
    url: str
    provider_id: str | None
    supported: bool
    unsupported_vendor: str | None


@dataclass(frozen=True)
class AtsJob:
    title: str
    url: str
    company: str
    location: str
    description: str | None
    posted_at: int | None
    external_id: str | None = None


@dataclass(frozen=True)
class AtsFetchResult:
    ok: bool
    provider_id: str
    jobs: list[AtsJob]
    error: str | None
    requests: int


def _http_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() in ("http", "https") and parsed.netloc:
        return cleaned
    return None


def _sanitized_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
    env["CAREER_OPS_HTTP_TIMEOUT_MS"] = "10000"
    return env


class AtsBridge:
    def __init__(
        self,
        providers_dir: Path | str,
        *,
        max_stdin_bytes: int = 262_144,
        max_stdout_bytes: int = 1_048_576,
        max_jobs_per_provider: int = 200,
        max_requests_per_provider: int = 25,
        max_total_requests: int = 250,
    ) -> None:
        self.providers_dir = Path(providers_dir)
        self.max_stdin_bytes = min(max(256, int(max_stdin_bytes)), 262_144)
        self.max_stdout_bytes = min(max(1024, int(max_stdout_bytes)), 1_048_576)
        self.max_jobs_per_provider = min(max(1, int(max_jobs_per_provider)), 200)
        self.max_requests_per_provider = min(max(1, int(max_requests_per_provider)), 25)
        self.max_total_requests = max(0, int(max_total_requests))
        self.total_requests = 0

    def _run_bridge(self, payload: dict, *, timeout_s: float) -> dict:
        raw_in = json.dumps(payload, ensure_ascii=False)
        if len(raw_in.encode("utf-8")) > self.max_stdin_bytes:
            raise ValueError(f"Bridge input exceeds {self.max_stdin_bytes} bytes")
        proc = subprocess.run(
            ["node", str(_BRIDGE_JS), str(self.providers_dir)],
            input=raw_in,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout_s,
            shell=False,
            env=_sanitized_env(),
        )
        stdout = proc.stdout or ""
        if len(stdout.encode("utf-8")) > self.max_stdout_bytes:
            raise ValueError(f"Bridge stdout exceeds {self.max_stdout_bytes} bytes")
        if proc.returncode != 0 and not stdout.strip():
            raise RuntimeError((proc.stderr or f"Bridge exited {proc.returncode}").strip()[:300])
        data = json.loads(stdout)
        if not isinstance(data, dict):
            raise ValueError("Bridge returned non-object JSON")
        return data

    def detect(self, urls: list[str], *, company: str = "") -> list[AtsMatch]:
        if not urls:
            return []
        try:
            data = self._run_bridge(
                {
                    "mode": "detect",
                    "urls": list(urls),
                    "company": company,
                    "max_stdout_bytes": self.max_stdout_bytes,
                },
                timeout_s=15.0,
            )
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return []
        if not data.get("ok") or not isinstance(data.get("matches"), list):
            return []
        matches: list[AtsMatch] = []
        for item in data["matches"]:
            if not isinstance(item, dict) or not isinstance(item.get("url"), str):
                continue
            pid = item.get("provider_id")
            if pid == "local-parser":
                continue
            matches.append(
                AtsMatch(
                    url=item["url"],
                    provider_id=pid if isinstance(pid, str) else None,
                    supported=bool(item.get("supported")),
                    unsupported_vendor=item.get("unsupported_vendor")
                    if isinstance(item.get("unsupported_vendor"), str)
                    else None,
                )
            )
        return matches

    def fetch(
        self,
        url: str,
        *,
        company: str,
        provider_id: str,
        timeout_s: float = 20.0,
        api_url: str | None = None,
    ) -> AtsFetchResult:
        if provider_id == "local-parser":
            return AtsFetchResult(False, provider_id, [], "local-parser is disallowed", 0)
        remaining_budget = self.max_total_requests - self.total_requests
        if remaining_budget <= 0:
            return AtsFetchResult(False, provider_id, [], "Global ATS bridge request budget exceeded", 0)

        bounded_timeout = min(max(0.2, float(timeout_s)), 20.0)
        per_call_requests = min(self.max_requests_per_provider, remaining_budget)
        payload = {
            "mode": "fetch",
            "url": url,
            "company": company,
            "provider_id": provider_id,
            "timeout_ms": int(bounded_timeout * 1000),
            "max_jobs": self.max_jobs_per_provider,
            "max_requests": per_call_requests,
            "max_stdout_bytes": self.max_stdout_bytes,
        }
        if api_url:
            payload["api_url"] = api_url

        try:
            data = self._run_bridge(payload, timeout_s=bounded_timeout + 2.0)
        except subprocess.TimeoutExpired:
            self.total_requests += 1
            return AtsFetchResult(False, provider_id, [], f"ATS bridge timeout after {bounded_timeout:.1f}s", 1)
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            return AtsFetchResult(False, provider_id, [], str(exc)[:300], 0)

        req_count = data.get("requests")
        requests = int(req_count) if isinstance(req_count, int) and req_count >= 0 else 0
        self.total_requests += requests

        if not data.get("ok"):
            err = data.get("error") if isinstance(data.get("error"), str) else "ATS fetch failed"
            return AtsFetchResult(False, provider_id, [], err[:300], requests)

        raw_jobs = data.get("jobs")
        if not isinstance(raw_jobs, list):
            return AtsFetchResult(False, provider_id, [], "Malformed jobs list from ATS bridge", requests)

        jobs: list[AtsJob] = []
        for entry in raw_jobs[: self.max_jobs_per_provider]:
            if not isinstance(entry, dict):
                continue
            title = entry.get("title")
            job_url = _http_url(entry.get("url"))
            if not isinstance(title, str) or not title.strip() or not job_url:
                continue
            desc = entry.get("description")
            if isinstance(desc, str):
                desc = desc.strip()[:4000] or None
            else:
                desc = None
            posted = entry.get("posted_at")
            posted_at = int(posted) if isinstance(posted, int) and not isinstance(posted, bool) else None
            jobs.append(
                AtsJob(
                    title=title.strip()[:240],
                    url=job_url,
                    company=(entry.get("company") if isinstance(entry.get("company"), str) else company).strip()[:160],
                    location=(entry.get("location") if isinstance(entry.get("location"), str) else "").strip()[:200],
                    description=desc,
                    posted_at=posted_at,
                    external_id=entry.get("external_id") if isinstance(entry.get("external_id"), str) else None,
                )
            )
        return AtsFetchResult(True, str(data.get("provider_id") or provider_id), jobs, None, requests)
