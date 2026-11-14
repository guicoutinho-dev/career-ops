from pydantic import BaseModel, HttpUrl, Field
from typing import Optional
from datetime import datetime, timezone

class JobCandidate(BaseModel):
    id: str
    source: str
    external_id: Optional[str] = None
    canonical_url: str
    employer: str
    title: str
    location: str
    description: str
    publication_date: Optional[str] = None
    discovered_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "DISCOVERED"
    content_hash: str
    last_verified_at: Optional[str] = None
