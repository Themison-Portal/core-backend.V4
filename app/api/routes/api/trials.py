"""
Trial routes — GET /, GET /{id}, POST /with-assignments, PUT /{id}
"""

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.trial import TrialResponse, TrialUpdate, TrialWithAssignmentsCreate
from app.dependencies.auth import get_current_member
from app.dependencies.db import get_db
from app.models.members import Member
from app.models.trial_members import TrialMember
from app.models.trial_members_pending import TrialMemberPending
from app.models.trials import Trial
from app.services.crud import CRUDBase

router = APIRouter()


@router.get("/", response_model=List[TrialResponse])
async def list_trials(
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    crud = CRUDBase(Trial, db)
    return await crud.get_multi(filters={"organization_id": member.organization_id})


@router.get("/{trial_id}", response_model=TrialResponse)
async def get_trial(
    trial_id: UUID,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    crud = CRUDBase(Trial, db)
    trial = await crud.get(trial_id)
    if not trial or trial.organization_id != member.organization_id:
        raise HTTPException(status_code=404, detail="Trial not found")
    return trial


@router.post("/with-assignments", response_model=TrialResponse, status_code=201)
async def create_trial_with_assignments(
    payload: TrialWithAssignmentsCreate,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    # Create trial
    trial_data = payload.model_dump(exclude={"members", "pending_members"})
    trial_data["organization_id"] = member.organization_id
    trial_data["created_by"] = member.id

    trial = Trial(**trial_data)
    db.add(trial)
    await db.flush()  # get trial.id

    # Create trial_members
    for assignment in payload.members:
        db.add(TrialMember(
            trial_id=trial.id,
            member_id=assignment.member_id,
            role_id=assignment.role_id,
        ))

    # Create trial_members_pending
    for pending in payload.pending_members:
        db.add(TrialMemberPending(
            trial_id=trial.id,
            invitation_id=pending.invitation_id,
            role_id=pending.role_id,
            invited_by=member.id,
        ))

    await db.commit()
    await db.refresh(trial)
    return trial


@router.put("/{trial_id}", response_model=TrialResponse)
async def update_trial(
    trial_id: UUID,
    payload: TrialUpdate,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    crud = CRUDBase(Trial, db)
    trial = await crud.get(trial_id)
    if not trial or trial.organization_id != member.organization_id:
        raise HTTPException(status_code=404, detail="Trial not found")

    updated = await crud.update(trial_id, payload.model_dump(exclude_unset=True))
    return updated
