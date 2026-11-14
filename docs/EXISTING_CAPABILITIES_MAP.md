# Existing Capabilities Map — JOB//HUNTER

**Audit date:** 2026-11-01  
**Status legend:** **Ready to reuse** = concrete implementation inspected; **Partial / gated** = implementation exists but policy, ownership, or runtime gaps remain; **Not verified** = prompt requirement is not supported by evidence in this checkout/runtime. This maps actual capabilities, not desired future behavior.

## Navigation-to-capability map

| JOB//HUNTER area | Existing capability and source | Current truth / integration route | Status and constraints |
|---|---|---|---|
| **01 Command Center** | Career-ops local web home/today dashboard, pipeline summary, doctor, analytics; Python `src/cli/status.py`; `src/services/health_check.py`; Python `events` and `tasks`; career-ops tracker/stats/funnel scripts | Extend existing `web/` home and read APIs; backend can aggregate read-only summaries from supported local files/SQLite. Use actual timestamps and source-status responses. | **Partial.** The necessary pieces exist but no single verified dashboard API joins Hermes runtime + Maestri + both data stores. Counts and task-state semantics differ. Hermes server not running at audit time. Do not show placeholders as live telemetry. |
| **02 Job Discovery** | Career-ops `scan.mjs`, `scan-ats-full.mjs`, `scan-ats-full` providers, liveness and URL dedup; web `/api/explore` streams reverse-ATS results. Python `ATSAggregatorAdapter` shells out to the Career-ops scanner and `DiscoveryService` deduplicates/ingests. JobSpy adapter and `scripts/jobspy_scan.py` exist. | Prefer the existing web Explore route or Python ATS adapter; normalize with existing data models; preserve original URL/source; use the existing deduplicator. | **Ready for public ATS path; JobSpy/SEEK gated.** Daily runner reports SEEK and JobSpy blocked; the AI-powered explorer is a proposer and its output is unverified. WHV assessment exists in Python `MatchAnalyst`/`job_classifier` but is not verified as a field consistently persisted/displayed for every scanned listing. Salary/date may be absent; show unknown rather than infer. |
| **03 Application Tracker** | Career-ops Markdown tracker, statuses, canonical `set-status.mjs` / `merge-tracker.mjs` and web tracker; Australia SQLite schema for applications/events/approvals/tasks; selected future JOB//HUNTER Obsidian vault. | For new JOB//HUNTER records, use one Markdown note per vacancy in the Obsidian vault and derive Kanban views from YAML properties. Preserve Career-ops and SQLite contracts behind an explicit adapter; start read-only/import-only until vault path, migration scope, stable IDs, conflict handling, and round-trip tests are settled. | **Target decided; integration gated.** Obsidian is the source of truth for new JOB//HUNTER lifecycle records. Career-ops still treats `data/applications.md` as canonical, and the Python extension uses SQLite for its richer lifecycle. Do not silently write status into multiple stores. |
| **04 CV Studio** | Career-ops modes and generators: `modes/pdf.md`, `build-cv-html.mjs`, `generate-latex.mjs`, `generate-pdf.mjs`, fact/ATS checks, cover-letter script, templates/fonts. Python application preparer and `ApprovalService` create hash-bound human review packages. Australia `tailor_packages.py` renders personal static role specs. | Use the existing Career-ops CV mode and renderer from a server-side worker; capture structured job/report ID and artifact path; run fact/ATS checks; preserve version/document records only through the owner of the chosen store. | **Partial.** Rendering and review mechanisms exist, but no verified generic Python API connects arbitrary job to tailored CV version history; Australia tailor code is hard-coded. Confirm the source-of-truth/fact-checking workflow and resolve existing PDF route write instructions before UI reuse. |
| **05 Agent Control** | Hermes runtime supports TUI gateway JSON-RPC over stdio/WebSocket, ACP over stdio, CLI and documented OpenAI-compatible API/SSE. Hermes installation, profiles, sessions and logs are separately managed. Career-ops uses local child processes for its existing worker actions. Maestri roles are defined in `.maestri/roles/*/role.json` and rules in `modes/_custom.md`. | Use a backend-owned Hermes protocol client for actual Hermes sessions/runs. Treat Maestri dispatch as separate until `maestri` CLI/API and role-to-runtime mapping are confirmed. Query exact current protocol capabilities for status, events, interrupt and approval. | **Partial / not verified.** `Antigravity_Mestre`, `Farol`, `Pergaminho`, and `Correio` are not established as Hermes agents in the active profile. `maestri` was not found on PATH. No `hermes serve` process was running. Do not fake agent availability or execution logs. |

## Cross-cutting capabilities

| Capability | Evidence | UI treatment |
|---|---|---|
| **Execution and logs** | Existing Python task queue/events and `DailyRunner` report; Career-ops web uses local child processes and streamed NDJSON/SSE-style responses; Hermes has its own runtime/session stores and logs. | Keep sources distinct and identify engine/run ID. A database task is not automatically a Hermes run. Show complete/failed/interrupted only from observed exit/state evidence. |
| **Human approval** | Python `ApprovalService` hashes application package artifacts and requires interactive review/confirmation; Career-ops apply flow is user-controlled; status and profile writes have explicit confirmation patterns. | Require review before application submission or other external consequence. Approval must bind to the current exact artifacts; edits invalidate prior approval. |
| **Gmail** | Gmail API adapter uses readonly OAuth scope; mock, live, dry-run, list, watch CLI; classification and application matching persisted in SQLite. | Display inbound events only. Do not offer compose/send as an existing capability. Keep OAuth material on the local backend. |
| **Notion** | Notion adapter can sync and upload PDFs and also pulls selected manual status changes; dry-run when token is absent. | Label as external sync, require confirmation, and report actual response/error. Do not describe Notion as strictly read-only. |
| **FLI / Flight Finder** | `fli` executable installed; search scripts use `fli flights ... --format json` and create JSON reports. | Optional travel-planning extension only; no verified integrated job-hunter API or persistent flight store. Show price source and observation time if exposed. |
| **Search configuration** | `config/search_profiles.yaml`, `config/sources.yaml`, and career-ops `portals.yml` supply existing search inputs. | Read configurations via safe server-side APIs. Keep secrets and candidate-specific settings off the client. Do not let a UI-maintained second agent/source registry diverge from configuration. |
| **Visual style** | `web/DESIGN.md` already contains a JOB//HUNTER-compatible dark palette, electric blue, condensed display face, monospace metadata, accessible focus, restrained effects, and light theme tokens. | Reuse its design rules and components; no need to add a second design system. |

## Capability-level status

### Available now (through existing code paths)

- Local Next.js/React/TypeScript UI with navigation, responsive patterns, Home/Today, Explore, Pipeline, CV, Analytics, Apply, Config and API routes.
- Public ATS discovery with existing filtering, result normalization, source metadata, URL handling, dedup and streamed run feedback.
- Existing job/application data shapes and SQL migrations; local SQLite persistence and backup/event/task mechanisms.
- Structured Python rule-based match assessment, hard blockers, and evidence output.
- Existing candidate-profile-based Career-ops evaluation, tailored CV/PDF, cover-letter, liveness, tracker, reports, and follow-up tools.
- Gmail readonly classification and a human-reviewed application package/approval gate.

### Available only with explicit gates or verification

- JobSpy source queries and SEEK access: config may list enabled but the daily workflow blocks requests pending authorization/policy review.
- Live data synchronization to Notion and upload operations.
- Hermes interactive control: use an official protocol, configure/run the service intentionally, and verify profile, auth, session scope, cancellation, and approvals.
- Maestri specialist execution: confirm the executable/API and role IDs on the target machine. The checkout contains role definitions, not proof of live workers.
- Obsidian integration: selected as the store for new JOB//HUNTER records, but the vault path, import scope, and non-destructive Career-ops/SQLite adapter are not configured or verified.
- Generic CV version history and job-to-document association: connect the existing render/review workflows to the chosen canonical domain store.

### Not verified / not presently an existing capability

- A Hermes agent registry containing the four named worker agents.
- A public, project-specific Hermes adapter or REST endpoint in the career-ops checkout.
- A running Hermes `serve` HTTP/WebSocket service at the time of audit.
- A verified single backend endpoint for Hermes health + Maestri availability + active tasks + logs.
- A FastAPI application entry point in the inspected Python `src/` tree; dependencies alone do not prove an API service exists.
- A Tauri desktop shell in the inspected manifests.
- A live integrated FLI screen/API for JOB//HUNTER.
- A policy-cleared JobSpy/SEEK scan route that may be safely triggered from the UI.

## UI action mapping and gates

| UI action | Existing invocation target | Requirement before exposing as a button |
|---|---|---|
| Start vacancy scan | Existing Career-ops `scan-ats-full.mjs` via web Explore or Python ATS adapter | Show configured ATS list, scan limits, partial/error states, and source URL; do not call JobSpy/SEEK until permission gate is cleared. |
| Analyze vacancy | Career-ops evaluation mode or Python `MatchAnalyst` | Make clear which evaluator is running; link output to source JD and keep external posting content untrusted. |
| Generate tailored CV | Career-ops PDF mode and established HTML/PDF renderer; optional Python app-preparation flow | Candidate facts must come from approved profile/CV; show diff/artifact and run factual/ATS validation; no submission. |
| Synchronize application tracking | New JOB//HUNTER records: Obsidian vault adapter. Existing Career-ops records: canonical Career-ops tracker operations. Existing extension records: current Python/SQLite service until migration is approved. | Configure vault path; begin read-only/import-only; define stable identity, owner per record, conflict handling, atomic writes/events, deduplication, backup, and exact readback before enabling any cross-store writes. |
| Sync Notion | Python `NotionAdapter` | Explicit confirmation, dry-run preview, and result readback; protect tokens and uploaded files. |
| Read Gmail | Python Gmail adapter `--live` / dry-run | Read-only scope and explicit display of message processing; no send path. |
| Execute Hermes | Supported TUI gateway JSON-RPC or configured HTTP/SSE API server | Server-side credentials, current protocol contract, live event/result verification, cancellation and approval semantics. |
| Execute Farol/Pergaminho/Correio | No verified invocation route beyond Maestri conventions and role files | Keep disabled/“not connected” until actual dispatch is proven; never alias them silently to generic Hermes chat. |
| Stop run / approve action | Hermes protocol or existing service-specific control | Verify the run belongs to the caller/session; bind approval to exact action/artifacts; distinguish interrupt request from confirmed stopped state. |

## Incremental implementation recommendation

1. Preserve the current web app; add only the missing control surfaces.
2. Locate/configure the Obsidian vault and settle the import scope and non-destructive adapter; independently settle the Hermes-vs-Maestri boundary.
3. Prototype a read-only Hermes connection and show only verified runtime/session information.
4. Add job-search actions through existing ATS/career-ops and Australia services, with blocked sources labelled honestly.
5. Add write controls for new JOB//HUNTER records to Obsidian only after vault configuration and backup/round-trip tests; keep existing Career-ops and SQLite write paths governed by their current contracts until a tested migration is approved.
6. Add visual polish by reusing `web/DESIGN.md`; do not add fake metrics or fake agent cards.

**Obsidian is the selected future JOB//HUNTER store. Vault writes/imports and agent-control implementation remain gated by the open items in `HERMES_INTEGRATION_AUDIT.md`.**
