from datetime import datetime

from pydantic import BaseModel, Field


class HelpRequestResponse(BaseModel):
    id: int
    agent_id: str | None
    question: str
    context: str | None
    status: str
    created_at: datetime
    resolved_at: datetime | None
    resolution: str | None


class ResolveRequest(BaseModel):
    resolution: str = Field(min_length=1)
