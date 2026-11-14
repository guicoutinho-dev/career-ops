"""Isolated read-only local preview launcher for SydneyStays V5."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import socket
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hub.api.app import AppDeps, create_app
from hub.domain.models import Run, Trigger
from hub.pipeline.routine import RunAlreadyActive
from hub.storage.catalog import CatalogRepository
from hub.storage.db import Database, migrate_published_catalog
from hub.storage.groups import GroupRepository
from hub.storage.posts import PostRepository
from hub.storage.runs import RunRepository
from web.build_dashboard import build as build_dashboard

DEFAULT_V4_SOURCE_DB = Path(
    r"C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops\sydney-hub\data\hub.db"
)
_ALLOWED_PREVIEW_HOSTS = {"127.0.0.1", "localhost"}
_SAFE_PREVIEW_METHODS = {"GET", "HEAD", "OPTIONS"}


class _PreviewDisabledRunner:
    """Stub runner that keeps preview status idle and refuses manual scrapes."""

    @property
    def is_running(self) -> bool:
        return False

    def run(self, trigger: Trigger) -> Run:
        raise RunAlreadyActive("Scraping is disabled in preview mode")


class _PreviewDisabledScheduler:
    """Stub scheduler returning a deterministic sentinel without starting background jobs."""

    def next_run_at(self) -> datetime:
        return datetime(2099, 1, 1, 0, 0, tzinfo=timezone.utc)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_preview_port(host: str, port: int) -> int:
    """Ensure host is loopback-only and the target TCP port is currently unbound."""
    if host not in _ALLOWED_PREVIEW_HOSTS:
        raise ValueError(f"Preview host must be loopback-only (127.0.0.1 or localhost), got {host!r}")
    if not (1 <= port <= 65535):
        raise ValueError(f"Port must be between 1 and 65535, got {port}")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise RuntimeError(f"Preview port {host}:{port} is already occupied")

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as bind_probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            bind_probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            bind_probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(f"Preview port {host}:{port} is already occupied") from exc

    return port


def copy_db_readonly(source_db: Path, dest_db: Path) -> dict[str, object]:
    """Snapshot source_db via SQLite read-only URI + .backup() without touching source."""
    source_path = Path(source_db)
    if not source_path.is_file():
        raise FileNotFoundError(f"Source SQLite database not found: {source_path}")

    source_resolved = source_path.resolve()
    dest_path = Path(dest_db)
    dest_resolved = dest_path.resolve()
    if dest_resolved == source_resolved:
        raise ValueError(f"Destination path must not match source database: {dest_path}")
    if os.path.lexists(dest_path) or os.path.lexists(dest_resolved):
        raise FileExistsError(f"Destination database already exists and will not be overwritten: {dest_path}")

    source_sha256 = _sha256(source_path)
    source_uri = f"{source_resolved.as_uri()}?mode=ro"

    dest_path.parent.mkdir(parents=True, exist_ok=True)

    with closing(sqlite3.connect(source_uri, uri=True)) as src_conn:
        integrity = src_conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"Source database failed integrity check: {integrity}")
        with closing(sqlite3.connect(dest_path)) as dst_conn:
            src_conn.backup(dst_conn)

    with closing(sqlite3.connect(dest_path)) as dst_check:
        dest_integrity = dst_check.execute("PRAGMA integrity_check").fetchone()[0]
        if dest_integrity != "ok":
            raise RuntimeError(f"Copied preview database failed integrity check: {dest_integrity}")
        version = dst_check.execute("PRAGMA user_version").fetchone()[0]

    db = Database(dest_path)
    with closing(sqlite3.connect(dest_path)) as ver_conn:
        version = ver_conn.execute("PRAGMA user_version").fetchone()[0]
    if version == 4:
        migrate_published_catalog(dest_path)

    summary = CatalogRepository(db).summary()
    return {
        "source_sha256": source_sha256,
        "dest_db": str(dest_path),
        "integrity": "ok",
        "counts_by_state": summary["counts_by_state"],
    }


def create_preview_app(db_path: Path, web_dir: Path, port: int) -> FastAPI:
    """Build the V5 FastAPI preview app with scraping, scheduling, and non-local hosts disabled."""
    with contextlib.redirect_stdout(io.StringIO()):
        build_dashboard(web_dir)

    db = Database(db_path)
    allowed_hosts = {
        f"127.0.0.1:{port}",
        f"localhost:{port}",
        "127.0.0.1",
        "localhost",
    }
    deps = AppDeps(
        groups=GroupRepository(db),
        posts=PostRepository(db),
        runs=RunRepository(db),
        catalog=CatalogRepository(db),
        runner=_PreviewDisabledRunner(),
        scheduler=_PreviewDisabledScheduler(),
        web_dir=web_dir,
        cv_dir=None,
        allowed_hosts=allowed_hosts,
    )
    app = create_app(deps)

    @app.middleware("http")
    async def preview_readonly_guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if host not in allowed_hosts:
            return JSONResponse({"detail": "Forbidden host"}, status_code=403)
        if request.method.upper() not in _SAFE_PREVIEW_METHODS:
            return JSONResponse(
                {"detail": "Write operations and manual runs are disabled in preview mode"},
                status_code=409,
            )
        return await call_next(request)

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only isolated local preview launcher for SydneyStays V5")
    parser.add_argument("--source-db", type=Path, default=DEFAULT_V4_SOURCE_DB)
    parser.add_argument("--port", type=int, default=8795)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--keep-copy", type=Path, default=None, help="Optional destination path for persistent preview DB copy")
    parser.add_argument("--once", action="store_true", help="Verify isolated DB copy and API endpoints, print JSON, and exit")
    args = parser.parse_args(argv)

    if args.host not in _ALLOWED_PREVIEW_HOSTS:
        raise SystemExit(f"Refusing non-loopback host {args.host!r}")
    if args.keep_copy is not None:
        if args.keep_copy.resolve() == args.source_db.resolve():
            raise SystemExit(f"Refusing --keep-copy pointing to source database: {args.keep_copy}")
        if os.path.lexists(args.keep_copy) or os.path.lexists(args.keep_copy.resolve()):
            raise SystemExit(f"Refusing --keep-copy to existing destination: {args.keep_copy}")
    if not args.once:
        validate_preview_port(args.host, args.port)

    web_dir = ROOT / "web"

    def _run_with_dest(dest_db: Path) -> int:
        copy_info = copy_db_readonly(args.source_db, dest_db)
        app = create_preview_app(dest_db, web_dir=web_dir, port=args.port)
        if args.once:
            client = TestClient(app, base_url=f"http://{args.host}:{args.port}")
            root_resp = client.get("/")
            summary_resp = client.get("/api/opportunities/summary")
            opps_resp = client.get("/api/opportunities?limit=500")
            if root_resp.status_code != 200 or summary_resp.status_code != 200 or opps_resp.status_code != 200:
                raise RuntimeError("Preview smoke check failed")
            print(
                json.dumps(
                    {
                        **copy_info,
                        "root_status": root_resp.status_code,
                        "summary": summary_resp.json(),
                        "opportunities_count": len(opps_resp.json()),
                    },
                    indent=2,
                )
            )
            return 0

        print(
            f"SydneyStays V5 isolated preview ready at http://{args.host}:{args.port} "
            f"(source_sha256={copy_info['source_sha256'][:12]}, counts={copy_info['counts_by_state']})",
            flush=True,
        )
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return 0

    if args.keep_copy is not None:
        return _run_with_dest(args.keep_copy)

    with tempfile.TemporaryDirectory(prefix="sydneystays_preview_") as tmp_dir:
        return _run_with_dest(Path(tmp_dir) / "preview_hub.db")


if __name__ == "__main__":
    sys.exit(main())
