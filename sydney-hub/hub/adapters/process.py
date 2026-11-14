"""Thin wrapper over subprocess so adapters can be tested with a fake runner."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ""


CommandRunner = Callable[..., CommandResult]


def subprocess_runner(args: list[str], timeout: float = 120, env: dict[str, str] | None = None) -> CommandResult:
    merged_env = {**os.environ, **env} if env else None
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env=merged_env,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(returncode=124, stdout="", stderr="timeout")
    except OSError as exc:
        return CommandResult(returncode=127, stdout="", stderr=str(exc))
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)


def resolve_maestri() -> str:
    return shutil.which("maestri") or os.environ.get("MAESTRI_CLI") or "maestri"
