from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone

class AuditEvent(BaseModel):
    id: Optional[int] = None
    entity_type: str
    entity_id: str
    event_type: str
    actor: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    correlation_id: str
    payload_json: Optional[str] = None
