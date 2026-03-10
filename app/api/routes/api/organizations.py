"""
Organization routes — GET/PUT /me, GET /me/metrics
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts.organization import (
    OrganizationMetrics,
    OrganizationResponse,
    OrganizationUpdate,
)
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

    members_count = (
        await db.execute(
            select(func.count())
            .select_from(Member)
            .where(Member.organization_id == org_id)
        )
    ).scalar_one()

    trials_count = (
        await db.execute(
            select(func.count())
            .select_from(Trial)
            .where(Trial.organization_id == org_id)
        )
    ).scalar_one()

    active_trials = (
        await db.execute(
            select(func.count())
            .select_from(Trial)
            .where(Trial.organization_id == org_id, Trial.status == "active")
        )
    ).scalar_one()

    patients_count = (
        await db.execute(
            select(func.count())
            .select_from(Patient)
            .where(Patient.organization_id == org_id)
        )
    ).scalar_one()

    # Documents count: trial_documents linked to this org's trials
    documents_count = (
        await db.execute(
            select(func.count())
            .select_from(Document)
            .where(
                Document.trial_id.in_(
                    select(Trial.id).where(Trial.organization_id == org_id)
                )
            )
        )
    ).scalar_one()

    return OrganizationMetrics(
        total_members=members_count,
        total_trials=trials_count,
        total_patients=patients_count,
        total_documents=documents_count,
        active_trials=active_trials,
    )


# -----------------------
# Admin endpoints for managing any org
# -----------------------


@router.get("/{org_id}", response_model=OrganizationResponse)
async def get_organization_by_id(
    org_id: str,
    current_user: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Get organization by ID.
    Only accessible by admin/superadmin roles.
    """
    if current_user.org_role not in ["superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    return org


@router.put("/{org_id}", response_model=OrganizationResponse)
async def update_organization_by_id(
    org_id: str,
    payload: OrganizationUpdate,
    current_user: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Update organization by ID.
    Only accessible by admin/superadmin roles.
    """
    if current_user.org_role not in ["superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalars().first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(org, key, value)

    await db.commit()
    await db.refresh(org)
    return org


@router.delete("/members/{member_id}", status_code=200)
async def delete_organization_member(
    member_id: str,
    current_user: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a member from the organization.
    Only allow if current_user is staff/admin/superadmin.
    """
    # Step 0: Check permissions (only superadmin/admin can delete)
    if current_user.org_role not in ["superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to delete members")

    # Step 1: Fetch the member to delete
    result = await db.execute(
        select(Member).where(
            Member.id == member_id,
            Member.organization_id == current_user.organization_id,
        )
    )
    member_to_delete = result.scalars().first()
    if not member_to_delete:
        raise HTTPException(status_code=404, detail="Member not found")

    # Step 2: Prevent deleting the last superadmin
    if member_to_delete.org_role == "superadmin":
        superadmin_count = (
            await db.execute(
                select(func.count())
                .select_from(Member)
                .where(
                    Member.organization_id == current_user.organization_id,
                    Member.org_role == "superadmin",
                )
            )
        ).scalar_one()
        if superadmin_count <= 1:
            raise HTTPException(
                status_code=400, detail="Cannot remove the last superadmin"
            )

    # Step 3: Optional - save snapshot (you can use JSON column if needed)
    user_snapshot = {
        "id": member_to_delete.id,
        "email": member_to_delete.email,
        "first_name": member_to_delete.first_name,
        "last_name": member_to_delete.last_name,
        "role": member_to_delete.org_role,
        "deleted_at": str(func.now()),
    }
    # You could store snapshot in a separate table if needed

    # Step 4: Delete the member (soft delete recommended)
    member_to_delete.deleted_at = func.now()  # soft delete
    await db.commit()
    await db.refresh(member_to_delete)

    return {"message": "Member deleted successfully", "snapshot": user_snapshot}


# -----------------------
# Console / Admin endpoints
# -----------------------


@router.get("/", response_model=list[OrganizationResponse])
async def list_organizations(
    current_user: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    List all organizations (console staff).
    """
    if current_user.org_role not in ["superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(Organization))
    organizations = result.scalars().all()

    return organizations


@router.post("/", response_model=OrganizationResponse, status_code=201)
async def create_organization(
    payload: OrganizationUpdate,
    current_user: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new organization.
    """
    if current_user.org_role not in ["superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    org = Organization(**payload.model_dump())

    db.add(org)
    await db.commit()
    await db.refresh(org)

    return org


@router.patch("/{org_id}", response_model=OrganizationResponse)
async def patch_update_organization(
    org_id: str,
    payload: OrganizationUpdate,
    current_user: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Partial update for organization (PATCH used by frontend).
    """
    if current_user.org_role not in ["superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalars().first()

    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(org, key, value)

    await db.commit()
    await db.refresh(org)

    return org


@router.delete("/{org_id}/members/{member_id}")
async def remove_member_from_org(
    org_id: str,
    member_id: str,
    current_user: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Remove member from organization (console).
    """
    if current_user.org_role not in ["superadmin", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(
        select(Member).where(
            Member.id == member_id,
            Member.organization_id == org_id,
        )
    )

    member = result.scalars().first()

    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    member.deleted_at = func.now()

    await db.commit()

    return {"message": "Member removed successfully"}
