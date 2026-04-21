"""
This module contains the contracts for the chat.
"""

from typing import List, Optional
from uuid import UUID

from .base import BaseContract, TimestampedContract
from .document import DocumentResponse


class ChatMessageBase(BaseContract):
    """
    A base contract for all chat messages.
    """

    content: str


class ChatMessageCreate(ChatMessageBase):
    """
    A contract for creating a chat message.
    """

    session_id: UUID


class ChatMessageResponse(ChatMessageBase, TimestampedContract):
    """
    A contract for a chat message.
    """

    id: UUID
    session_id: UUID


class ChatSessionBase(BaseContract):
    """
    A base contract for all chat sessions.
    """

    title: str


class ChatSessionCreate(ChatSessionBase):
    """
    A contract for creating a chat session.
    """

    trial_id: Optional[UUID] = None
    document_id: Optional[UUID] = None
    document_name: Optional[str] = None


class ChatSessionUpdate(BaseContract):
    """
    A contract for updating a chat session.
    """

    title: Optional[str] = None


class ChatSessionResponse(ChatSessionBase, TimestampedContract):
    """
    A contract for a chat session.
    """

    id: UUID
    user_id: UUID
    trial_id: Optional[UUID] = None
    document_id: Optional[UUID] = None
    document_name: Optional[str] = None
    messages: List[ChatMessageResponse]
    documents: List[DocumentResponse]
