from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime, timezone

RoleTrack = Literal["sap", "operational"]
ApplicationStatus = Literal[
    "DISCOVERED", "DEDUPLICATED", "QUALIFIED", "DISQUALIFIED",
    "DOCUMENTS_DRAFTED", "REVIEW_PENDING", "REVIEW_PASSED", "REVIEW_FAILED",
    "PENDING_APPROVAL", "APPROVED", "READY_TO_SUBMIT", "APPLIED",
    "ACKNOWLEDGED", "SCREENING", "INTERVIEW", "ASSESSMENT", "OFFER",
    "CLOSED_WON", "CLOSED_LOST", "NEEDS_INFORMATION", "FOLLOW_UP_DUE",
    "EXPIRED", "WITHDRAWN", "BLOCKED"
]

class Application(BaseModel):
    id: str
    job_id: str
    profile_id: str
    role_track: RoleTrack
    match_score: Optional[float] = None
    confidence: Optional[float] = None
    status: ApplicationStatus = "DISCOVERED"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    submitted_at: Optional[str] = None
    next_action_at: Optional[str] = None
    assigned_owner: Optional[str] = None
