"""Fail-closed opt-in scheduler for Published Jobs (separate from Facebook SchedulerService)."""
from __future__ import annotations

import logging
import threading
import time as time_module
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from hub.discovery.health import SourceHealthStore
from hub.discovery.pilot_gate import evaluate_pilot_gates
from hub.pipeline.routine import RunAlreadyActive
from hub.scheduler.slots import next_slot, parse_slots
from hub.settings import JobsConfig
from hub.storage.db import check_isolated_jobs_db_ready
from run_published_jobs import run_published_jobs


log = logging.getLogger(__name__)
_SYDNEY_TZ = ZoneInfo("Australia/Sydney")


@dataclass(frozen=True)
class SchedulerGateResult:
    allowed: bool
    reasons: tuple[str, ...]


def evaluate_scheduler_gate(
    config: JobsConfig,
    *,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
) -> SchedulerGateResult:
    """Require opt-in, 1-2 slots, isolated v5 DB, 20/10/5, 7 real days, and 100% per-listing audit."""
    pj = config.published_jobs
    reasons: list[str] = []

    if not pj.scheduler_enabled:
        reasons.append("scheduler_disabled")

    if not (1 <= len(pj.schedule) <= 2):
        reasons.append("invalid_schedule_slots")
    else:
        try:
            parse_slots(pj.schedule)
        except ValueError:
            reasons.append("invalid_schedule_slots")

    db_path = config.resolve(pj.db_path)
    isolated_root = config.resolve(pj.isolated_db_dir)
    db_ok, db_reason = check_isolated_jobs_db_ready(db_path, isolated_root=isolated_root)
    if not db_ok and db_reason:
        reasons.append(db_reason)

    scorecard = evaluate_pilot_gates(
        db_path,
        config.resolve(pj.state_dir),
        config.resolve(pj.reports_dir),
        config.resolve(pj.boards_path),
        config.resolve(pj.policies_path),
        now=now,
        env=env,
    )
    if not scorecard.first_run_passed:
        reasons.append("first_run_targets_not_met")
    if scorecard.seven_day_status != "passed":
        reasons.append("seven_day_observation_pending")
    if not scorecard.manual_audit_confirmed:
        reasons.append("manual_audit_unconfirmed")

    return SchedulerGateResult(allowed=not reasons, reasons=tuple(reasons))


class PublishedSchedulerService:
    """Opt-in APScheduler for isolated Published Jobs; stays inactive until all gates pass."""

    def __init__(
        self,
        config: JobsConfig,
        run_fn: Callable[..., dict] | None = None,
        clock: Callable[[], float] = time_module.time,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._config = config
        self._run_fn = run_fn or self._default_run
        self._clock = clock
        self._env = env
        self._run_lock = threading.Lock()
        self._scheduler = BackgroundScheduler(timezone=_SYDNEY_TZ)

    def _default_run(self, *, skip_workable: bool = False) -> dict:
        pj = self._config.published_jobs
        return run_published_jobs(
            self._config.resolve(pj.db_path),
            self._config.resolve(pj.boards_path),
            live=True,
            max_verifications=pj.max_verifications,
            policies_path=self._config.resolve(pj.policies_path),
            state_dir=self._config.resolve(pj.state_dir),
            reports_dir=self._config.resolve(pj.reports_dir),
            isolated_root=self._config.resolve(pj.isolated_db_dir),
            skip_workable=skip_workable,
            geocode_street_addresses=pj.geocode_street_addresses,
            origin_label=pj.origin_label,
        )

    @property
    def is_running(self) -> bool:
        return self._run_lock.locked()

    @property
    def is_active(self) -> bool:
        return bool(self._scheduler.running)

    @property
    def gate_result(self) -> SchedulerGateResult:
        now_dt = datetime.fromtimestamp(self._clock(), tz=_SYDNEY_TZ)
        return evaluate_scheduler_gate(self._config, now=now_dt, env=self._env)

    def next_run_at(self) -> datetime | None:
        gate = self.gate_result
        if not self.is_active or not gate.allowed:
            return None
        slots = parse_slots(self._config.published_jobs.schedule)
        now_local = datetime.fromtimestamp(self._clock(), tz=_SYDNEY_TZ).replace(tzinfo=None)
        return next_slot(now_local, slots).replace(tzinfo=_SYDNEY_TZ)

    def _should_skip_workable(self) -> bool:
        pj = self._config.published_jobs
        if not pj.workable_enabled:
            return True
        store = SourceHealthStore(self._config.resolve(pj.state_dir))
        last_fetch = store.last_workable_fetch()
        if last_fetch is None:
            return False
        return (self._clock() - last_fetch) < pj.workable_min_interval_hours * 3600

    def trigger_manual(self) -> dict:
        if not self._run_lock.acquire(blocking=False):
            raise RunAlreadyActive("A published jobs run is already active")
        try:
            skip_workable = self._should_skip_workable()
            return self._run_fn(skip_workable=skip_workable)
        finally:
            self._run_lock.release()

    def _fire(self) -> None:
        try:
            self.trigger_manual()
        except RunAlreadyActive:
            log.info("Skipping scheduled published jobs run: another run is active")
        except Exception:
            log.exception("Scheduled published jobs run failed")

    def start(self) -> bool:
        gate = self.gate_result
        if not gate.allowed:
            return False
        if self._scheduler.running:
            return True
        slots = parse_slots(self._config.published_jobs.schedule)
        for slot in slots:
            self._scheduler.add_job(
                self._fire,
                CronTrigger(hour=slot.hour, minute=slot.minute, timezone=_SYDNEY_TZ),
                misfire_grace_time=600,
                coalesce=True,
                max_instances=1,
                id=f"jobs-slot-{slot.hour:02d}{slot.minute:02d}",
                replace_existing=True,
            )
        self._scheduler.start()
        return True

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
