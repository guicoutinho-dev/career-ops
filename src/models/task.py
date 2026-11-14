from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime, timezone

TaskStatus = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "RETRYING"]

class TaskRecord(BaseModel):
    id: str
    task_type: str
    entity_id: str
    status: TaskStatus = "PENDING"
    attempts: int = 0
    idempotency_key: str
    scheduled_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_code: Optional[str] = None
