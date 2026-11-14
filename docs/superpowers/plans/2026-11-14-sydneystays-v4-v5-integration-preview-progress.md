# SydneyStays V4 + V5 Integration & Preview Ledger

**Execution Date:** 2026-11-14
**Approved Checkout:** `D:\AntigravityProjetos\sydneystays-v4-v5-preview`
**Approved Branch:** `codex/sydneystays-v4-v5-preview`

## Environment Substitution Ruling

- **Plan vs Approved Environment:** The plan referenced worktree `C:\Users\guicx\.codex\worktrees\sydneystays-v5-preview\career-ops` and branch `codex/sydneystays-v4-v5-integrated`. Per user prompt and Maestri note `SydneyStays Preview`, execution ran in `D:\AntigravityProjetos\sydneystays-v4-v5-preview` on branch `codex/sydneystays-v4-v5-preview` (branched from `f0f73d63`, linear descendant of `d1d08591`).
- **Local `node_modules`:** Linked via NTFS junction to `D:\AntigravityProjetos\career-ops\node_modules` (gitignored).

## Verified Git SHAs & Backup Refs

| Ref | Full SHA | Notes |
|-----|----------|-------|
| `backup/v4-d1d08591` | `d1d08591c265dbadd5ebbc287b87c1e7f51fc303` | Entrega 4 baseline preserved |
| `backup/v5-f0f73d63` | `f0f73d63ad86267bb625897d2038c4a76112a0b6` | Entrega 5 baseline (7 commits ahead of V4) preserved |
| Task 1 (`chore(security): ...`) | `12f294d0bb2c589e4a75e0908dc6bd61fe0791ad` | `.gitignore`, `sydney-hub/check_publish_hygiene.py`, `sydney-hub/tests/test_publish_hygiene.py` |
| Task 2 (`feat(preview): ...`) | `bc3fd8af939fad347a75a9420abaa9ba0390eb6f` | `sydney-hub/preview_v5.py`, `sydney-hub/tests/test_preview_v5.py` |
| Task 3 (`test(preview): ...`) | `1eea42927624bc24b5f0d5c76b6580e5fd2f39ec` | `sydney-hub/tests/test_preview_e2e.mjs` |

## Credential & PII Hygiene Audit (`check_publish_hygiene.py`)

- **`HEAD` (`codex/sydneystays-v4-v5-preview`):** `is_safe_to_publish = False`
  - `forbidden_tracked`: `945` files, including `config/gmail_auth_url.txt`, `config/gmail_credentials.json`, `config/gmail_token.json`, `USER.md`, and `applications/*`.
  - `sensitive_history_commits`: includes ancestor `d31174e536ae8df9df0405cf6e9501963e950196`.
  - **Direct `git push` of this branch is blocked.**
- **`b532a48d` (`fork/codex/sydneystays-employer-pilot`):** `is_safe_to_publish = False`
  - Excludes `config/gmail_*` (`gmail: []`) and `d31174e5` (`has_d31174e5: False`), **but still tracks `942` `USER.md` / `applications/*` files** from `f38f6e193dae1618978252178aaf972176e56ae3`.
- **Required Before Any Future GitHub Push:**
  1. Rotate the Gmail OAuth client secret and refresh/access tokens in Google Cloud Console (treat historical tokens in local Git history as compromised).
  2. Export a clean orphan/sanitized snapshot excluding `config/gmail_*`, `USER.md`, `applications/`, `*.db`, `.pii-token-map.json`, `sydney-hub/data/`, and `sites/`, verified by `python sydney-hub/check_publish_hygiene.py --ref <branch>` returning exit `0` (`is_safe_to_publish: True`).

## Published Site Deployment Map (Untouched)

- **Checkout:** `D:\AntigravityProjetos\career-ops\sites\sydneystays-publish\.sites-checkout` (HEAD `a8772ef`)
- **Hosting Config (`.openai/hosting.json`):** `project_id: "appgprj_6af5ae2c3fec819192072cc5032e363a"`, `"static": {"directory": "dist"}`
- **Sibling Static Bundle:** `D:\AntigravityProjetos\career-ops\sites\sydney-opportunity-hub\dist\index.html`
- **Architecture Gap:** Published site serves a static inline bundle without `/api/opportunities` backend endpoints. Dropping V5 `web/index.html` into `dist/` as-is would break live loading.
- **Migration Decision Options (Pending Explicit User Decision):**
  - **Option A (Recommended for now):** Keep ChatGPT Sites static bundle untouched.
  - **Option B:** Add an explicit static snapshot exporter for `dist/` in a follow-up delivery.
  - **Option C:** Deploy FastAPI backend + managed DB separately and point frontend API origin cleanly.

## Original Databases & Artifacts Integrity (Zero Mutation Verified)

| Artifact | SHA-256 / Observed State | LastWriteTime |
|----------|--------------------------|---------------|
| V4 Pilot DB (`C:\Users\guicx\.codex\worktrees\sydneystays-employer-pilot\career-ops\sydney-hub\data\hub.db`) | `0B8BA88BCC656A7D4312BBE02243957CDC1B8BBF49EFCA9FB8946E18CC2A457F` | `2026-11-14 12:12:03` |
| Main Worktree v2 DB (`D:\AntigravityProjetos\career-ops\sydney-hub\data\hub.db`) | `F0E13096464FF683B2263D90098768A718645E9BEF16B4BAA5FD6C7C150B11FC` | `2026-11-13 23:18:14` |
| `D:\AntigravityProjetos\career-ops\sites\sydneystays-publish` | Untouched | `2026-11-13 17:43:22` |
| `D:\AntigravityProjetos\career-ops\sites\sydney-opportunity-hub` | Untouched | `2026-11-12 17:01:29` |

## Automated Verification Results

- `python -m pytest -ra`: **258 passed, 2 skipped** (`osmium` optional dependency in `test_osm_pbf.py` and `test_employer_pilot.py`).
- `node --test tests/test_jobs_v5_engine.mjs`: **5 passed, 0 failed**.
- `node tests/test_jobs_ui.mjs http://127.0.0.1:8798` (via `tests/serve_ui_fixture.py 8798`): **passed (exit 0)**.
- `node tests/test_preview_e2e.mjs http://127.0.0.1:8796 --mode=leads-only` (isolated V4 copy): **passed (`155 leads, 0 community, 0 verified`)**.
- `node tests/test_preview_e2e.mjs http://127.0.0.1:8797 --mode=mixed` (isolated main v2 copy): **passed (`20 leads, 130 community, 0 verified`)**.
- `impeccable.cmd detect --json web/template.html web/atlas.css`: **0 warnings in `web/atlas.css`**; only 2 pre-existing baseline warnings in `web/template.html` (`tiny-text`, `cream-palette`).

## Review Fix Pass (RED → GREEN)

1. **(Critical) Fail-closed CLI hygiene gate (`check_publish_hygiene.py`):** Removed `--allow-history-ancestor-only` bypass flag; `test_cli_fails_closed_on_sensitive_history_and_rejects_bypass_flag` verified RED → GREEN.
2. **(Critical) Safe `--keep-copy` / `copy_db_readonly` guards (`preview_v5.py`):** Rejected `dest == source` (which previously hung in SQLite `.backup()` self-lock) and pre-existing destinations (`os.path.lexists`) prior to directory creation or SQLite open; `test_copy_db_readonly_and_keep_copy_reject_source_or_existing_dest` verified RED (timeout/hang) → GREEN.
3. **(Important) Full HTTP mutation block in preview (`preview_v5.py`):** `preview_readonly_guard` now rejects all non-`GET`/`HEAD`/`OPTIONS` requests (including `POST /api/posts/seen`, `PATCH /api/posts/{id}`, `PUT`, `DELETE`) with `HTTP 409`; `test_preview_app_blocks_all_http_mutations_including_posts_without_breaking_get` verified RED (`204 != 409`) → GREEN.
4. **(Important) Wildcard `config/gmail_*` coverage (`check_publish_hygiene.py`, `.gitignore`):** Switched `FORBIDDEN_TRACKED_PATTERNS`, `_HISTORY_PATHSPECS` (`:(glob)config/gmail_*`), `.gitignore`, and dirty scan (`git status --porcelain=v1 -z -uall`) to catch arbitrary `config/gmail_*` files; `test_hygiene_flags_arbitrary_config_gmail_wildcard_in_tree_history_and_dirty` verified RED → GREEN.

## Rollback Verification & Commands

1. **Code rollback to V4 (`d1d08591`):**
   ```powershell
   git -C D:\AntigravityProjetos\sydneystays-v4-v5-preview reset --hard backup/v4-d1d08591
   ```
   Or remove preview checkout: `git worktree remove D:\AntigravityProjetos\sydneystays-v4-v5-preview`.
2. **Database rollback drill (verified on isolated temp copies):**
   - `v2 -> v4 -> v2`: `python rollback_catalog.py --database <copy.db> --target-version 2 --force` restored `user_version=2`, `PRAGMA integrity_check == 'ok'`, and preserved newer writes in `*.before-rollback-*.db`.
   - `v4 -> v5 -> v4`: `restore_published_catalog_backup(<copy.db>, force=True)` restored `user_version=4`, `PRAGMA integrity_check == 'ok'`, and preserved newer writes in `*.before-rollback-*.db`.
3. **ChatGPT Sites rollback:** Not needed (`sites/` remained untouched; `project_id: appgprj_6af5ae2c3fec819192072cc5032e363a`).
