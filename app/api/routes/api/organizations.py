"""
Organization routes — GET/PUT /me, GET /me/metrics
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.organization import OrganizationMetrics, OrganizationResponse, OrganizationUpdate
from app.dependencies.auth import get_current_member
from app.dependencies.db import get_db
from app.models.members import Member
from app.models.organizations import Organization
from app.models.patients import Patient
from app.models.trials import Trial
from app.models.documents import Document

router = APIRouter()


@router.get("/me", response_model=OrganizationResponse)
async def get_my_organization(
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Organization).where(Organization.id == member.organization_id)
    )
    org = result.scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@router.put("/me", response_model=OrganizationResponse)
async def update_my_organization(
    payload: OrganizationUpdate,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Organization).where(Organization.id == member.organization_id)
    )
    org = result.scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(org, key, value)

    await db.commit()
    await db.refresh(org)
    return org


@router.get("/me/metrics", response_model=OrganizationMetrics)
async def get_organization_metrics(
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    org_id = member.organization_id

    members_count = (await db.execute(
        select(func.count()).select_from(Member).where(Member.organization_id == org_id)
    )).scalar_one()

    trials_count = (await db.execute(
        select(func.count()).select_from(Trial).where(Trial.organization_id == org_id)
    )).scalar_one()

    active_trials = (await db.execute(
        select(func.count()).select_from(Trial).where(
            Trial.organization_id == org_id, Trial.status == "active"
        )
    )).scalar_one()

    patients_count = (await db.execute(
        select(func.count()).select_from(Patient).where(Patient.organization_id == org_id)
    )).scalar_one()

    # Documents count: trial_documents linked to this org's trials
    documents_count = (await db.execute(
        select(func.count()).select_from(Document).where(
            Document.trial_id.in_(
                select(Trial.id).where(Trial.organization_id == org_id)
            )
        )
    )).scalar_one()

    return OrganizationMetrics(
        total_members=members_count,
        total_trials=trials_count,
        total_patients=patients_count,
        total_documents=documents_count,
        active_trials=active_trials,
    )
