"""
Invitation routes — GET /count, POST /batch

Invitation token validation — GET /invitations/validate/{token}
"""

from typing import List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
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
from app.models.organizations import Organization
from app.services.email_service import email_service

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


@router.get("/validate/{token}")
async def validate_invitation_token(token: str, db: AsyncSession = Depends(get_db)):
    """
    Validate an invitation token for signup (new users).

    Returns invitation details if valid.
    Raises 404/400 if token is invalid, expired, already used, or does not exist.
    """
    result = await db.execute(select(Invitation).where(Invitation.token == token))
    invitation = result.scalars().first()

    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found",
        )

    if invitation.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invitation is not pending (current status: {invitation.status})",
        )

    if invitation.expires_at and invitation.expires_at < datetime.utcnow():
        # Optionally, mark invitation as expired in DB
        invitation.status = "expired"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invitation has expired",
        )

    # Fetch organization details
    org_result = await db.execute(select(Organization).where(Organization.id == invitation.organization_id))
    org = org_result.scalars().first()
    
    # Format according to Frontend expectations
    return {
        "id": str(invitation.id),
        "email": invitation.email,
        "org_id": str(invitation.organization_id),
        "org_role": invitation.initial_role,
        "organization": {
            "id": str(invitation.organization_id),
            "name": org.name if org else "Themison"
        },
        "name": invitation.name,
        "expires_at": (
            invitation.expires_at.isoformat() if invitation.expires_at else None
        )
    }


@router.get("/count", response_model=InvitationCountResponse)
async def get_invitation_counts(
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    org_id = member.organization_id

    pending = (
        await db.execute(
            select(func.count())
            .select_from(Invitation)
            .where(Invitation.organization_id == org_id, Invitation.status == "pending")
        )
    ).scalar_one()

    accepted = (
        await db.execute(
            select(func.count())
            .select_from(Invitation)
            .where(
                Invitation.organization_id == org_id, Invitation.status == "accepted"
            )
        )
    ).scalar_one()

    expired = (
        await db.execute(
            select(func.count())
            .select_from(Invitation)
            .where(Invitation.organization_id == org_id, Invitation.status == "expired")
        )
    ).scalar_one()

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
        existing = (
            (
                await db.execute(
                    select(Invitation).where(
                        Invitation.organization_id == org_id,
                        Invitation.email == item.email,
                        Invitation.status == "pending",
                    )
                )
            )
            .scalars()
            .first()
        )

        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"Pending invitation already exists for {item.email}",
            )

        inv = Invitation(
            email=item.email,
            name=item.name,
            organization_id=org_id,
            initial_role=item.org_role,
            invited_by=member.id,
        )
        db.add(inv)
        created.append(inv)

    # Fetch organization name for the email
    org_result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = org_result.scalars().first()
    org_name = org.name if org else "Themison"

    await db.commit()
    
    # Refresh and send emails
    for inv in created:
        await db.refresh(inv)
        # Trigger email (asynchronous logs for now)
        await email_service.send_invitation_email(
            email=inv.email,
            name=inv.name,
            token=inv.token,
            org_name=org_name
        )
        
    return created
