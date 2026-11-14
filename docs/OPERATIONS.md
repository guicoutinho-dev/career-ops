# Australia Job Hunter — Operational Runbook

## 1. Daily Search Workflow
1. **Scheduled Execution:** `uv run python -m src.cli.daily_run` scans enabled search profiles against the public Greenhouse, Lever, Ashby, and Workday directories using `scan-ats-full.mjs --dry-run --json --shuffle`, then imports unseen results into SQLite. It also ingests `data/pipeline.md`, imports tracker rows, evaluates pending DB jobs, backs up SQLite, and writes a daily report.
2. **Source boundaries:** Explore and `DailyRunner` query public ATS directories only.
   - SEEK is paused in `config/sources.yaml` until API approval.[1] No request is made.
   - The configured JobSpy Indeed/Australia route is blocked by its robots policy.[3]
   - LinkedIn automated access is blocked without express permission.[4]
   - Explore reports per-source status; SEEK and JobSpy requests are not made.
3. **Deduplication:** ATS discoveries are inserted transactionally and deduplicated against existing `canonical_url` and `content_hash` in the `jobs` table. Duplicates do not overwrite existing rows.
4. **Evaluation:** Unseen jobs are classified by category before Match Analyst selects its scoring track; missing descriptions stay in human review rather than being scored as a match.
5. **Draft Generation, Review and Approval:** CV tailoring, Revisor-Gy audit, and human approval are separate workflow stages; the current `daily_run` does not invoke them automatically. No application is submitted without explicit human approval.

## 2. Operator CLI Commands
- `python -m src.cli.status`: Displays pipeline counts, active tasks, and pending approvals.
- `python -m src.cli.approve <application_id>`: Issues a cryptographically signed approval token.
- `uv run python -m src.integrations.career_ops.sync`: One-way legacy import into SQLite; never run as a reverse sync or allow it to overwrite existing SQLite application state.

## 3. Crash Recovery and Reconciliation
- On startup, the task orchestrator checks the `tasks` table for any tasks in `RUNNING` state whose heartbeat has expired (> 15 minutes).
- Stale tasks are reset to `PENDING` with incremented attempt counters or marked `FAILED` if retry limits are exceeded.

## Sources

[1] https://developer.seek.com
[3] https://au.indeed.com/robots.txt
[4] https://www.linkedin.com/robots.txt
