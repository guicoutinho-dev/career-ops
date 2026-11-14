# Sydney Hub — Agent Rules

These rules apply to every agent working inside `sydney-hub/`. Read `ARCHITECTURE.md` first.

## Architecture
- Respect the Modular Monolith and the module-boundary table in `ARCHITECTURE.md`.
- `hub/domain` is pure: no I/O, no imports from other `hub` modules.
- Only `hub/adapters/*` may call the Maestri CLI, PowerShell, or the network.
- Only `hub/storage` imports `sqlite3`. Only `app.py` wires concrete adapters.
- If a change needs a boundary violation, update `ARCHITECTURE.md` and ask the user first.

## Process
- **TDD:** write a failing test, make it pass, then refactor. No production code without a test
  for `domain`, `classify`, `pipeline` and `storage`.
- Small, single-purpose functions; descriptive names; no dead code; no commented-out code.
- Preserve existing comments and docstrings unrelated to your change.
- Type-annotate all public functions; validate external input with pydantic.

## Safety (non-negotiable)
- Never bypass, solve or wait out a login checkpoint, CAPTCHA, or anti-bot challenge. On such a
  signal: stop the routine, mark the run `needs_attention`, notify the user.
- Never store Facebook credentials or cookies in the repo, DB or logs.
- Never send messages, post, react or comment on the user's behalf. Read-only scraping.
- Keep scraping serial with 2–5 s delays and at most the configured runs per day.
- Bind the API to `127.0.0.1` only. Never put secrets in HTML/JS.

## Data
- Runtime data lives in `data/` (git-ignored, user layer). Never commit `hub.db` or logs.
- Do not touch career-ops system-layer files from this project.

## UI
- Follow the `design-taste-frontend` skill: calm, minimal, no emoji decoration, no purple/violet,
  clean Inter typography, tabular numbers, real controls only (no fake filters).
- Every control must be wired to real behaviour and have loading, empty and error states.

## Language
- Code, comments and commit messages in English. User-facing chat replies in pt-BR.
