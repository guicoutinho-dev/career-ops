# Hermes Integration Audit — JOB//HUNTER

**Audit date:** 2026-11-01  
**Scope:** read-only audit of the current checkout and the active Hermes installation/profile. No application architecture or runtime code was changed.

## Executive summary

This checkout is a hybrid workspace, not a blank Australia Job Hunter frontend: it contains the mature career-ops Node/Next.js product plus an Australia-specific Python service layer, SQLite schema, adapters, and Maestri role definitions. There is already a sizeable local web interface and child-process integration layer. Replacing either would duplicate working infrastructure.

Hermes Agent is installed and its default-profile gateway is running, but no Hermes `serve`/dashboard process is running. The active Hermes profile is `codex`; its config names a model/provider but does not define the named Australia Job Hunter agents as Hermes agents. Those worker labels are represented by project-local Maestri role files and instructions. The `maestri` executable was not found on PATH, so their live dispatch cannot be verified from this environment.

**Recommended boundary:** keep the current Next.js UI and existing career-ops/Australia services as the job-search and data boundary. Add a small local backend integration for Hermes only after confirming whether the app needs full session/approval control or simple model runs. For full control, Hermes documents the TUI gateway JSON-RPC protocol over stdio/WebSocket as the custom-host interface; ACP is for IDE clients, and the OpenAI-compatible HTTP/SSE API is a simpler but narrower alternative. Do not use Hermes internals or invent an endpoint. Do not represent the Maestri role names as Hermes-discoverable agents until an actual supported bridge/configuration is found.

**Phase A outcome: audit artifacts are ready; Phase B is not cleared.** Product decision recorded: Obsidian Markdown vault is the desired source of truth for JOB//HUNTER records and Kanban views. The existing Career-ops tracker remains governed by its own canonical-file contract; integration must be explicit and non-destructive. The vault path is not configured/discovered in this environment, and the Hermes-vs-Maestri execution boundary remains unresolved.

## 1. Repository and technology inventory

- **Repository root:** `D:/AntigravityProjetos/career-ops`, branch `main`. The worktree contains many untracked Australia Job Hunter files (including `src/`, `applications/`, `.maestri/`, and additions under `web/`). These were treated as user-owned; none were staged, modified, or removed.
- **Career-ops core:** Node.js ESM, package `career-ops` v1.38.0; Node engine `^22.13.0 || >=23.4.0`. The repository supplies many scripts, prompt modes, ATS providers, CV/report generation, and tracker workflows.
- **Existing web app:** `web/` is Next.js 16.2, React 19, TypeScript 6, with server routes that call local career-ops scripts and render pipeline, discovery, CV, application-preparation, and analytics screens. It already has a local server launcher (`scripts/ui-server.mjs`, default loopback `127.0.0.1:3000`). `web/DESIGN.md` already specifies the requested electric-blue / dark / restrained retro-futurist visual language. There is no Tauri dependency or shell in the inspected manifests.
- **Australia-specific service layer:** Python >=3.12, Pydantic, FastAPI/Uvicorn declared as dependencies, PyYAML, and `python-jobspy`; the inspected `src/` tree contains CLI commands/services/integrations but no FastAPI application/router entry point. FastAPI is therefore a declared dependency, not a verified running HTTP API.
- **Hermes:** installed locally as Hermes Agent v0.22.5 (reported build `bf0203a794a0`), using the `codex` profile. The profile config exposes model/provider/display/runtime settings; credentials were not inspected. `hermes status` reported the gateway running through the default-profile multiplexer and no messaging platforms configured. `hermes serve --status` reported no Hermes serve/dashboard process. These are a point-in-time runtime observation, not a persistent availability contract.
- **Database:** `data/applications.db` exists. Python uses SQLite with foreign keys, WAL mode, busy timeout, and versioned SQL migrations. The initial schema covers jobs, applications, documents, reviews, approvals, inbound email metadata, events, durable tasks, and migrations. `data/applications.db` is ignored by Git.
- **CV artifacts:** Career-ops offers HTML/LaTeX templates, fact/ATS checks, cover-letter generation and Playwright PDF rendering. Australia-specific artifacts include application directories and tailored templates; `src/services/tailor_packages.py` currently contains hard-coded role specifications and should not be treated as a general job-tailoring API.

## 2. Hermes integration interfaces (verified)

Hermes official documentation: [Programmatic Integration](https://hermes-agent.nousresearch.com/docs/developer-guide/programmatic-integration), [API Server](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server), and [CLI](https://hermes-agent.nousresearch.com/docs/reference/cli-commands). The locally installed CLI also confirms `hermes serve`, `hermes acp`, and `hermes dashboard` commands.

| Interface | Supported use | Fit for JOB//HUNTER |
|---|---|---|
| **TUI gateway JSON-RPC** over stdio or WebSocket | Hermes documents this for custom hosts needing sessions, prompt streaming, slash commands, approvals, and session lifecycle. The protocol is the same family used by the Ink TUI/Desktop. | **Best fit if the product must control Hermes sessions and approvals.** Implement behind a local backend, follow the protocol/capability/auth handshake for the installed version, and keep credentials out of browser code. Verify agent/session listing methods and cancellation against the current protocol before promising them. |
| **OpenAI-compatible API server** (HTTP + SSE) | Hermes documents `/v1/chat/completions`, `/v1/responses`, `/v1/runs` and lifecycle/event routes, including stop/approval where enabled. It must be explicitly configured and started; API key and CORS are server-side concerns. | Good for a simple agent-run/chat surface with streaming and run status. It does not by itself establish that Maestri workers are Hermes agents or provide the same complete session-control surface as the TUI gateway. Prefer backend-to-backend calls; never ship its key to React. |
| **ACP** (`hermes acp`) | JSON-RPC over stdio for IDE clients implementing Agent Client Protocol. | Not the natural protocol for a standalone career desktop UI unless the UI deliberately implements ACP. |
| **CLI** (`hermes`, `hermes chat -q`, `hermes serve`) | Supported interactive/headless execution and server lifecycle. Career-ops docs permit Hermes one-shot mode only for expressly non-writing workers; they say there is no verified Hermes permission adapter for evaluation or portal-repair workers and batch ranking rejects Hermes. | Useful for bounded, explicitly non-writing operations and health/launch checks. Not a substitute for a structured execution protocol, approvals, or control-plane event stream. Do not treat terminal output as reliable structured agent telemetry. |
| **Direct Python `AIAgent` embedding** | Mentioned by official integration documentation as a Python in-process option. | Avoid for the first integration: it couples the adapter to Hermes internals and duplicates the process boundary. Only revisit if the published contract and packaging make this demonstrably simpler. |

The `hermes serve` CLI advertises a headless JSON-RPC/WebSocket backend for the desktop app and remote clients. For a custom host, use the documented TUI gateway protocol rather than assuming the serve port exposes arbitrary REST endpoints. The current profile has no API-server-specific setup verified in the inspected config. No server was started during this audit.

## 3. Agents, tools, and workflows

### Hermes versus project-local agents

- The active Hermes profile has model/provider/runtime configuration, but the inspected profile config does not expose an `Antigravity_Mestre`, `Farol`, `Pergaminho`, or `Correio` Hermes agent registry.
- `.maestri/roles/*/role.json` defines four Maestri worker roles: **Especialista Gmail Notion**, **Especialista Busca**, **Especialista Analise**, and **Revisor Independente**. These role definitions describe responsibilities and ownership; they are not Hermes agent definitions.
- `modes/_custom.md` describes **Farol**, **Pergaminho**, **Revisor-Gy**, **Correio**, and **Antigravity_Mestre**, and asks for delegation using `maestri ask`. This is the project’s orchestration convention, not evidence that Hermes hosts those agents.
- The `maestri` command was not found on PATH in this audit; live IPC, online state, stop controls, logs, and exact worker aliases could not be tested. Do not present four functional Hermes agent cards as verified until an execution bridge is confirmed.

### Verified job-search / career workflows

- **Career-ops ATS discovery:** `scan.mjs`, `scan-ats-full.mjs`, `providers/`, and the web Explore route provide the mature public-ATS path. The Python ATS adapter invokes `scan-ats-full.mjs --dry-run --json`, normalizes results, deduplicates, and ingests them into SQLite.
- **JobSpy:** `src/integrations/jobspy/adapter.py` wraps `python-jobspy`; `scripts/jobspy_scan.py` provides a direct scan-and-ingest path. However, `src/scheduler/daily_runner.py` explicitly reports JobSpy as blocked pending provider authorization/policy clearance and makes no request. `src/services/explore_jobspy.py` also reports blocked/disabled and returns no offers. The configured `config/sources.yaml` says enabled, but that setting does not override the runtime policy gate. The UI must show this as blocked, not as a successful live search.
- **SEEK:** SEEK-native discovery is disabled in config and the daily runner states that approved API access is required; no unofficial request is made.
- **Evaluation and candidate fit:** Python `MatchAnalyst` computes structured role-track fit, WHV/citizenship/clearance blockers, evidence, and confidence; Career-ops also provides its own A–F report evaluation modes and standalone model integrations. These are distinct engines and should not be silently conflated.
- **Applications and approvals:** Python CLI/service includes package preparation, hash-bound manual approval, status reporting, durable tasks, and event logging. Career-ops supplies canonical tracker scripts and web actions. External submission remains human-controlled in the documented policies.
- **Gmail:** Python Gmail adapter reads messages using Gmail API readonly credentials, classifies/matches messages, and can update SQLite statuses; CLI offers mock, live, dry-run, list, and watch modes. It is not a send-mail integration.
- **Notion:** Python Notion adapter has dry-run behavior when no token is present and performs external synchronization when configured. The role definition calls Notion a panel; actual code includes outbound sync and a pull path for manual updates, so treat it as a consequential external integration and gate it explicitly.
- **Flights / FLI:** `fli` is installed and exposes `airports`, `dates`, `flights`, and `multi`; repository scripts run it and write flight-search JSON under `output/`. This is a separate travel-planning workflow, not part of job discovery or the core application tracker. No JOB//HUNTER backend route was verified for it.
- **Other existing tools:** Career-ops also includes liveness checks, filtering, deduplication, tracker/status operations, reporting, interview/follow-up utilities, PDF/CV generation, and optional plugins.

## 4. Persistence and data ownership

There are at least three existing stores, plus the selected Obsidian target:

1. **Career-ops canonical files** — `data/applications.md`, `data/pipeline.md`, reports and user profile files. Root `ARCHITECTURE.md` explicitly states that human-readable files are the permanent source of truth and SQLite is a derived index.
2. **Australia Job Hunter SQLite extension** — `docs/DATA_MODEL.md`, `migrations/001_initial_schema.sql`, and Python services describe `data/applications.db` as authoritative for extension lifecycle records. Its tables support application status, documents, reviews, approvals, emails, events, and tasks.
3. **External Notion workspace** — an integration/view with both push and pull behavior in the Python adapter; not a substitute for local durable records.
4. **Obsidian vault (selected future JOB//HUNTER store)** — user-directed source of truth for new JOB//HUNTER vacancy/application lifecycle records, represented as one Markdown note per vacancy plus separate run notes. `OBSIDIAN_VAULT_PATH` was empty and `~/Documents/Obsidian Vault` did not exist during this audit, so no vault was modified or initialized. Kanban is a view over note properties, not another copy of the records.

**Compatibility boundary:** the Obsidian decision governs records created/managed by JOB//HUNTER; it does not silently repeal Career-ops's explicit `data/applications.md` source-of-truth contract or the Python extension's existing SQLite lifecycle. Start with non-destructive import/read views. Before any cross-store write, define per-record ownership, stable IDs, conflict resolution, and a tested idempotent adapter. Do not write a status independently to the vault, Career-ops tracker, and SQLite.

The Python `tasks` and `events` tables provide durable task state and audit history, but the code inspected does not establish them as a Hermes execution ledger. Hermes has its own profile/session storage. Keep those stores separate and link execution IDs to domain records only through a deliberate adapter schema.

## 5. Security, consent, and operational findings

- Keep Hermes and provider credentials server-side; no frontend/browser access to config files, `.env`, OAuth files, or Hermes internal state.
- Hermes server authentication and exact WebSocket/API authorization must follow the current official protocol. A public bind must not be treated as authenticated merely because `--insecure` is present; local CLI help calls it a no-op/deprecated.
- Existing repository guidance prohibits unattended/external application submission. Application submission, email sending, Notion writes/uploads, and document publication need explicit review/approval UX. Gmail is documented/code-configured readonly; Notion is not read-only in the adapter.
- The live Hermes runtime was observed but no `serve` API was running. Current status data should be polled from supported health/session interfaces, not inferred from a stored config file.
- Credential filenames `config/gmail_credentials.json` and `config/gmail_token.json` appeared in `git status` as untracked, and `git check-ignore` did not match them. Their contents were not opened. Treat them as sensitive and do not stage them; add a narrow ignore rule or move them to an already ignored, appropriate secret location after owner review.
- The checkout has a large untracked user working set. Do not run cleanup, formatter, migrations, migrations-on-the-live database, or broad staging during the next phase.
- `src/services/tailor_packages.py` includes hard-coded personal/job-specific output; it is not an appropriate generic UI API without a separate review.
- The existing web `/api/run` contains worker instructions for PDF/evaluation that need review against the core’s tracker write contract before reuse for new mutations. Prefer canonical scripts and verified artifact checks over prompt-only assurances.

## 6. Recommended integration architecture

1. **Retain `web/` as the starting UI.** It is already React/TypeScript and has API routes, streaming, discovery, pipeline, CV, configuration, status, and application preparation surfaces. Extend it incrementally rather than creating another React app or introducing Tauri without a desktop requirement proof.
2. **Use a local server-side integration boundary.** Frontend calls same-origin Next.js routes; routes validate typed actions and call a dedicated local adapter. The browser never spawns arbitrary commands, reads credentials, or talks to SQLite/Hermes stores directly.
3. **Hermes control choice:**
   - If the UI needs session creation/resume, live events, steering/interruption, and approval prompts, implement a backend client for the documented TUI gateway JSON-RPC over stdio/WebSocket and protocol-version it against the installed Hermes release.
   - If it needs only isolated model runs with event/status retrieval, evaluate the officially configured HTTP/SSE API server instead.
   - Keep any `hermes chat -q` CLI worker constrained to explicitly non-writing use until permission enforcement and cancellation are verified.
4. **Keep job domain logic local to existing systems.** Call the public ATS scanner and established Python services; reuse their result schemas, policy gates, deduplication, canonical files, and SQLite database. Do not reproduce scoring or invent source data in React.
5. **Do not fabricate agent registry.** First prove whether Maestri can be launched/controlled on this host and how the current aliases map to its role IDs. If it remains a separate runtime, expose it through a separate, explicit adapter and label its execution source accurately; do not call those workers Hermes agents.
6. **Use the Obsidian vault for new JOB//HUNTER records.** Store one Markdown note per vacancy and separate execution records; derive Kanban views from YAML properties. Do not add another database for this lifecycle. Preserve existing Career-ops/SQLite contracts behind a deliberate adapter, initially read-only/import-only, with idempotency, audit events, backups, and verified readback before enabling writes.
7. **Gate every external side effect.** Require user confirmation for outbound Notion changes, submissions, email sends, uploads/publication, and any action that changes application status. Show real run IDs, status, and log-derived results; never simulated success.

## 7. Open decisions / Phase B gates

- Is the intended agent control for Hermes sessions, Maestri worker dispatch, or both? The checked-in data does not establish these as one runtime.
- Which concrete Obsidian vault path should the local backend use? `OBSIDIAN_VAULT_PATH` was unset and the documented fallback was absent at audit time.
- Which existing records should be imported into Obsidian first, and what review process should resolve duplicates/status conflicts? No migration is authorized by the storage decision alone.
- Is a desktop shell required now? Tauri is not present in inspected manifests; the current local Next.js app already supplies a UI/server boundary.
- Which currently configured data sources are legally/policy enabled? The active daily runner intentionally blocks JobSpy/SEEK despite the JobSpy config’s `enabled: true` field.
- What approved status/approval path should the UI use for Notion changes and other external side effects?

Until the vault path and import scope are confirmed, stop before vault writes or migration. Until the agent boundary is verified from supported runtime interfaces, no Hermes endpoint, worker registry, live job results, or Maestri execution capability should be assumed.

## Sources inspected

Repository: `package.json`, `pyproject.toml`, `ARCHITECTURE.md`, `docs/HERMES.md`, `docs/SUPPORTED_CLIS.md`, `docs/DATA_MODEL.md`, `docs/DEPENDENCY_MAP.md`, `docs/SECURITY.md`, `web/package.json`, `web/README.md`, `web/DESIGN.md`, `src/database/`, `migrations/001_initial_schema.sql`, `src/services/`, `src/integrations/`, `src/cli/`, `scripts/`, `web/src/app/api/`, `.maestri/roles/*/role.json`, `modes/_custom.md`, and `config/sources.yaml`.

Runtime: `hermes --version`, `hermes --help`, `hermes serve --help`, `hermes acp --help`, `hermes dashboard --help`, `hermes serve --status`, `hermes status`, `fli --help`, and `command -v` checks. Hermes protocol claims cross-checked against the official documentation linked above.
