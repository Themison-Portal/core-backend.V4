"""
Contracts for organizations.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from .base import BaseContract, TimestampedContract


class OrganizationBase(BaseContract):
    name: str
    onboarding_completed: Optional[bool] = False


class OrganizationResponse(OrganizationBase, TimestampedContract):
    id: UUID
    created_by: UUID


class OrganizationUpdate(BaseContract):
    name: Optional[str] = None
    onboarding_completed: Optional[bool] = None


class OrganizationMetrics(BaseContract):
    total_members: int = 0
    total_trials: int = 0
    total_patients: int = 0
    total_documents: int = 0
    active_trials: int = 0
