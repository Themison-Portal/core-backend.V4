"""
Invitation model — maps to the invitations table.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import ENUM, UUID
from sqlalchemy.orm import Mapped

from .base import Base


class Invitation(Base):
    __tablename__ = "invitations"

    id: Mapped[UUID] = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = Column(Text, nullable=False)
    name: Mapped[str] = Column(Text, nullable=False)
    organization_id: Mapped[UUID] = Column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    initial_role: Mapped[str] = Column(
        ENUM("admin", "staff", name="organization_member_type", create_type=False),
        nullable=False,
    )
    status: Mapped[Optional[str]] = Column(Text, default="pending")
    invited_by: Mapped[Optional[UUID]] = Column(UUID(as_uuid=True), nullable=True)
    invited_at: Mapped[Optional[datetime]] = Column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[Optional[datetime]] = Column(DateTime(timezone=True), nullable=True)
    accepted_at: Mapped[Optional[datetime]] = Column(DateTime(timezone=True), nullable=True)
