"""
Invitation routes — GET /count, POST /batch
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.invitation import (
    InvitationBatchCreate,
    InvitationCountResponse,
    InvitationResponse,
)
from app.dependencies.auth import get_current_member
from app.dependencies.db import get_db
from app.models.invitations import Invitation
from app.models.members import Member

router = APIRouter()


@router.get("/", response_model=List[InvitationResponse])
async def list_invitations(
    status: str = None,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    org_id = member.organization_id
    query = select(Invitation).where(Invitation.organization_id == org_id)

    if status:
        query = query.where(Invitation.status == status)

    query = query.order_by(Invitation.invited_at.desc())

    result = await db.execute(query)
    return result.scalars().all()


@router.get("/count", response_model=InvitationCountResponse)
async def get_invitation_counts(
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    org_id = member.organization_id

    pending = (await db.execute(
        select(func.count()).select_from(Invitation).where(
            Invitation.organization_id == org_id, Invitation.status == "pending"
        )
    )).scalar_one()

    accepted = (await db.execute(
        select(func.count()).select_from(Invitation).where(
            Invitation.organization_id == org_id, Invitation.status == "accepted"
        )
    )).scalar_one()

    expired = (await db.execute(
        select(func.count()).select_from(Invitation).where(
            Invitation.organization_id == org_id, Invitation.status == "expired"
        )
    )).scalar_one()

    return InvitationCountResponse(
        pending=pending,
        accepted=accepted,
        expired=expired,
        total=pending + accepted + expired,
    )


@router.post("/batch", response_model=List[InvitationResponse], status_code=201)
async def batch_create_invitations(
    payload: InvitationBatchCreate,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    org_id = member.organization_id
    created = []

    for item in payload.invitations:
        # Check email uniqueness within org
        existing = (await db.execute(
            select(Invitation).where(
                Invitation.organization_id == org_id,
                Invitation.email == item.email,
                Invitation.status == "pending",
            )
        )).scalars().first()

        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Pending invitation already exists for {item.email}",
            )

        inv = Invitation(
            email=item.email,
            name=item.name,
            organization_id=org_id,
            initial_role=item.initial_role,
            invited_by=member.id,
        )
        db.add(inv)
        created.append(inv)

    await db.commit()
    for inv in created:
        await db.refresh(inv)
    return created
