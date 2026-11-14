"""Multi-agent Orchestration Ledger (P0 Requirement Enforcement)."""
import json
import time
import uuid
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

LEDGER_PATH = Path("data/orchestration-runs.jsonl")

class OrchestrationRun:
    def __init__(self, objective: str):
        self.run_id = f"run_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.objective = objective
        self.started_at = datetime.datetime.now().isoformat()
        self.tasks: List[Dict[str, Any]] = []
        self.solo_tasks: List[Dict[str, Any]] = []

    def plan_task(
        self,
        task_id: str,
        role: str,
        agent_name: str,
        description: str,
        expected_artifact: Optional[str] = None,
        dependencies: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        task = {
            "task_id": task_id,
            "role": role,
            "agent_name": agent_name,
            "description": description,
            "expected_artifact": expected_artifact,
            "dependencies": dependencies or [],
            "state": "planned",
            "dispatch_method": f'maestri ask "{agent_name}"',
            "dispatched_at": None,
            "completed_at": None,
            "artifact_path": None,
            "validation_verdict": None,
            "error": None
        }
        self.tasks.append(task)
        return task

    def record_solo_task(self, task_id: str, description: str, justification: str):
        self.solo_tasks.append({
            "task_id": task_id,
            "description": description,
            "justification": justification,
            "timestamp": datetime.datetime.now().isoformat()
        })

    def mark_dispatched(self, task_id: str):
        for t in self.tasks:
            if t["task_id"] == task_id:
                t["state"] = "dispatched"
                t["dispatched_at"] = datetime.datetime.now().isoformat()
                break

    def mark_completed(self, task_id: str, artifact_path: Optional[str] = None, validation_verdict: Optional[str] = "PASS"):
        for t in self.tasks:
            if t["task_id"] == task_id:
                t["state"] = "completed"
                t["completed_at"] = datetime.datetime.now().isoformat()
                t["artifact_path"] = artifact_path
                t["validation_verdict"] = validation_verdict
                break

    def mark_failed(self, task_id: str, error: str):
        for t in self.tasks:
            if t["task_id"] == task_id:
                t["state"] = "failed"
                t["error"] = error
                break

    def compute_metrics(self) -> Dict[str, Any]:
        eligible = len(self.tasks)
        dispatched = sum(1 for t in self.tasks if t["state"] in ("dispatched", "completed", "failed"))
        completed = sum(1 for t in self.tasks if t["state"] == "completed")
        verified = sum(1 for t in self.tasks if t.get("validation_verdict") == "PASS")
        delegation_rate = (dispatched / eligible * 100.0) if eligible > 0 else 100.0
        completion_rate = (completed / dispatched * 100.0) if dispatched > 0 else 0.0

        return {
            "run_id": self.run_id,
            "objective": self.objective,
            "total_tasks": eligible,
            "dispatched_tasks": dispatched,
            "completed_tasks": completed,
            "verified_artifacts": verified,
            "delegation_rate_pct": round(delegation_rate, 1),
            "completion_rate_pct": round(completion_rate, 1),
            "unjustified_solo_executions": len([s for s in self.solo_tasks if not s.get("justification")]),
            "solo_executions_count": len(self.solo_tasks)
        }

    def close(self) -> Dict[str, Any]:
        metrics = self.compute_metrics()
        record = {
            "run_id": self.run_id,
            "objective": self.objective,
            "started_at": self.started_at,
            "ended_at": datetime.datetime.now().isoformat(),
            "tasks": self.tasks,
            "solo_tasks": self.solo_tasks,
            "metrics": metrics
        }
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return metrics
