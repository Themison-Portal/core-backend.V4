"""
This module contains the base contracts for the application.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class BaseContract(BaseModel):
    """
    A base contract for all contracts.
    """
    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=lambda s: "".join(
            word.capitalize() if i > 0 else word for i, word in enumerate(s.split("_"))
        ),
        populate_by_name=True,
    )


class TimestampedContract(BaseContract):
    """
    A base contract for all contracts that have a created_at and updated_at field.
    """
    created_at: datetime
    updated_at: Optional[datetime] = None 