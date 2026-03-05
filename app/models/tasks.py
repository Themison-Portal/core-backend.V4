# app/models/tasks.py
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
from app.dependencies.db import Base  # your declarative base


class Task(Base):
    __tablename__ = "tasks"

    id = Column(UUID(as_uuid=True), primary_key=True)
    trial_id = Column(UUID(as_uuid=True), ForeignKey("trials.id"), nullable=False)
    status = Column(String, nullable=False)
    due_date = Column(DateTime, nullable=True)
    assigned_to = Column(UUID(as_uuid=True), ForeignKey("members.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # relationships
    trial = relationship("Trial", back_populates="tasks")
    assigned_user = relationship("Member", back_populates="tasks")
