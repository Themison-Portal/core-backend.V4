"""
Chat message routes — GET /, POST /
"""

from datetime import datetime, timezone
from typing import Dict, List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.chat import ChatMessageCreate
from app.dependencies.auth import get_current_member
from app.dependencies.db import get_db
from app.models.chat_messages import ChatMessage
from app.models.chat_sessions import ChatSession
from app.models.members import Member

router = APIRouter()


@router.get("/", response_model=List[Dict])
async def list_messages(
    session_id: UUID,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
    )
    messages = result.scalars().all()
    return [
        {
            "id": str(m.id),
            "session_id": str(m.session_id),
            "content": m.content,
            "role": m.role,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        }
        for m in messages
    ]


@router.post("/", status_code=201)
async def create_message(
    payload: ChatMessageCreate,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    # Verify session exists
    session = (await db.execute(
        select(ChatSession).where(ChatSession.id == payload.session_id)
    )).scalars().first()

    if not session:
        raise HTTPException(status_code=404, detail="Chat session not found")

    msg = ChatMessage(
        session_id=payload.session_id,
        content=payload.content,
        role="user",
    )
    db.add(msg)

    # Update session.updated_at
    session.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(msg)
    return {
        "id": str(msg.id),
        "session_id": str(msg.session_id),
        "content": msg.content,
        "role": msg.role,
        "created_at": msg.created_at.isoformat() if msg.created_at else None,
    }
