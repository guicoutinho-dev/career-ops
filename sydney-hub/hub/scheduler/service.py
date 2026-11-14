from __future__ import annotations

import logging
import time as time_module
from datetime import datetime, time
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from hub.domain.models import RunStatus, Trigger
from hub.pipeline.routine import RoutineRunner, RunAlreadyActive
from hub.scheduler.slots import needs_catchup, next_slot
from hub.storage.runs import RunRepository

log = logging.getLogger(__name__)


class SchedulerService:
    def __init__(
        self,
        runner: RoutineRunner,
        runs: RunRepository,
        slots: list[time],
        clock: Callable[[], float] = time_module.time,
    ) -> None:
        self._runner, self._runs, self._slots, self._clock = runner, runs, slots, clock
        self._scheduler = BackgroundScheduler()

    def next_run_at(self) -> datetime:
        return next_slot(datetime.fromtimestamp(self._clock()), self._slots)

    def _fire(self, trigger: Trigger) -> None:
        try:
            self._runner.run(trigger)
        except RunAlreadyActive:
            log.info("Skipping %s run: another routine is active", trigger.value)
        except Exception:
            log.exception("Routine crashed")

    def run_catchup_if_needed(self) -> bool:
        last = self._runs.last_finished_at([RunStatus.OK, RunStatus.PARTIAL])
        if needs_catchup(datetime.fromtimestamp(self._clock()), self._slots, last):
            self._fire(Trigger.CATCHUP)
            return True
        return False

    def start(self) -> None:
        for slot in self._slots:
            self._scheduler.add_job(
                self._fire, CronTrigger(hour=slot.hour, minute=slot.minute),
                args=[Trigger.SCHEDULE], misfire_grace_time=600, coalesce=True, max_instances=1,
                id=f"slot-{slot.hour:02d}{slot.minute:02d}",
            )
        self._scheduler.add_job(self.run_catchup_if_needed, id="catchup")
        self._scheduler.start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
