"""
Organization model — maps to the organizations table.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped

from .base import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = Column(Text, nullable=False)
    created_by: Mapped[UUID] = Column(
        UUID(as_uuid=True), ForeignKey("themison_admins.id"), nullable=False
    )
    created_at: Mapped[Optional[datetime]] = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[Optional[datetime]] = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    onboarding_completed: Mapped[Optional[bool]] = Column(Boolean, default=False)
