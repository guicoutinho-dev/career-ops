from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime, timezone

ApprovalDecision = Literal["PENDING", "APPROVED", "REJECTED"]

class ApprovalPackage(BaseModel):
    application_id: str
    employer: str
    job_title: str
    canonical_url: str
    job_description_hash: str
    match_score: float
    cv_filename: str
    cv_hash: str
    cover_letter_filename: str
    cover_letter_hash: str
    application_answers: dict = Field(default_factory=dict)
    work_auth_declarations: list[str] = Field(default_factory=list)

class ApprovalRecord(BaseModel):
    id: str
    application_id: str
    package_hash: str
    decision: ApprovalDecision = "PENDING"
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    invalidated_at: Optional[str] = None
