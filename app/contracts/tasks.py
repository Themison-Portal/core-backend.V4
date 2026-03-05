from pydantic import BaseModel
from typing import Optional
from uuid import UUID
from datetime import datetime


class AssignedUser(BaseModel):
    id: UUID
    full_name: Optional[str]


class TaskResponse(BaseModel):
    id: UUID
    trial_id: UUID
    status: str
    due_date: Optional[datetime]
    assigned_to: Optional[UUID]
    assigned_user: Optional[AssignedUser]
    created_at: datetime
    updated_at: datetime
