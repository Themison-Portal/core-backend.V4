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


# ============================================================================
# My Organization (accessible by both staff and admin)
# ============================================================================


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


# ============================================================================
# Console / Staff endpoints — staff only
# ============================================================================


@router.get("/", response_model=list[OrganizationResponse])
async def list_organizations(
    current_user: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """List all organizations — staff only."""
    # ✅ Fixed: was org_role not in ["superadmin", "admin"]
    if current_user.default_role != "staff":
        raise HTTPException(status_code=403, detail="Staff access only")

    result = await db.execute(select(Organization))
    return result.scalars().all()


@router.post("/", response_model=OrganizationResponse, status_code=201)
async def create_organization(
    payload: OrganizationUpdate,
    current_user: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """Create a new organization — staff only."""
    # ✅ Fixed: was org_role not in ["superadmin", "admin"]
    if current_user.default_role != "staff":
        raise HTTPException(status_code=403, detail="Staff access only")

    org = Organization(**payload.model_dump())
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return org


@router.get("/{org_id}", response_model=OrganizationResponse)
async def get_organization_by_id(
    org_id: str,
    current_user: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Get organization by ID.
    Staff can view any org.
    Admin can only view their own org.
    """
    # ✅ Fixed: was org_role not in ["superadmin", "admin"]
    if (
        current_user.default_role != "staff"
        and str(current_user.organization_id) != org_id
    ):
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
    Staff can update any org.
    Admin can only update their own org.
    """
    # ✅ Fixed: was org_role not in ["superadmin", "admin"]
    if (
        current_user.default_role != "staff"
        and str(current_user.organization_id) != org_id
    ):
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


@router.patch("/{org_id}", response_model=OrganizationResponse)
async def patch_update_organization(
    org_id: str,
    payload: OrganizationUpdate,
    current_user: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Partial update for organization.
    Staff can patch any org.
    Admin can only patch their own org.
    """
    # ✅ Fixed: was org_role not in ["superadmin", "admin"]
    if (
        current_user.default_role != "staff"
        and str(current_user.organization_id) != org_id
    ):
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
    Both staff and admin can delete members.
    """
    # ✅ Fixed: was org_role not in ["superadmin", "admin"]
    if current_user.default_role not in ["staff", "admin"]:
        raise HTTPException(status_code=403, detail="Not authorized to delete members")

    result = await db.execute(
        select(Member).where(
            Member.id == member_id,
            Member.organization_id == current_user.organization_id,
        )
    )
    member_to_delete = result.scalars().first()
    if not member_to_delete:
        raise HTTPException(status_code=404, detail="Member not found")

    # ✅ Fixed: was checking org_role == "superadmin" which doesn't exist
    # Prevent deleting the last admin in an org
    if member_to_delete.default_role == "admin":
        admin_count = (
            await db.execute(
                select(func.count())
                .select_from(Member)
                .where(
                    Member.organization_id == current_user.organization_id,
                    Member.default_role == "admin",
                )
            )
        ).scalar_one()
        if admin_count <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot remove the last admin of an organization",
            )

    # ✅ Fixed: was using member_to_delete.first_name/last_name
    # which don't exist on Member model — only name exists
    user_snapshot = {
        "id": str(member_to_delete.id),
        "email": member_to_delete.email,
        "name": member_to_delete.name,  # ✅ Member model has 'name' not first/last
        "role": member_to_delete.default_role,
    }

    member_to_delete.deleted_at = func.now()
    await db.commit()
    await db.refresh(member_to_delete)

    return {"message": "Member deleted successfully", "snapshot": user_snapshot}


@router.delete("/{org_id}/members/{member_id}")
async def remove_member_from_org(
    org_id: str,
    member_id: str,
    current_user: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """Remove member from organization — staff and admin."""
    # ✅ Fixed: was org_role not in ["staff", "admin"]
    if current_user.default_role not in ["staff", "admin"]:
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
