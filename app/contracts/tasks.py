from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


# -----------------------
# Assigned User Info
# -----------------------
class AssignedUser(BaseModel):
    id: UUID
    full_name: str


# -----------------------
# Task Response Model
# -----------------------
class TaskResponse(BaseModel):
    id: UUID
    trial_id: UUID
    status: str
    due_date: Optional[date]
    assigned_to: Optional[UUID]
    assigned_user: Optional[AssignedUser]
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True


# -----------------------
# Task Create Model
# -----------------------
class TaskCreate(BaseModel):
    trial_id: UUID
    title: str
    description: Optional[str] = None
    status: Optional[str] = "todo"
    priority: Optional[str] = None
    assigned_to: Optional[UUID] = None
    due_date: Optional[date] = None
    patient_id: Optional[UUID] = None
    visit_id: Optional[UUID] = None
    activity_type_id: Optional[UUID] = None

    class Config:
        orm_mode = True


# -----------------------
# Task Update Model
# -----------------------
class TaskUpdate(BaseModel):
    title: Optional[str]
    description: Optional[str]
    status: Optional[str]
    priority: Optional[str]
    assigned_to: Optional[UUID]
    due_date: Optional[date]
    patient_id: Optional[UUID]
    visit_id: Optional[UUID]
    activity_type_id: Optional[UUID]

    class Config:
        orm_mode = True
