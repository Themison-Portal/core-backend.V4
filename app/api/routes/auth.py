"""
Authentication routes
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_member, auth
from app.dependencies.db import get_db
from app.models.members import Member

router = APIRouter()


@router.get("/me")
async def get_me(
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the current user's profile, member, and organization data.
    """
    # Eagerly load related profile and organization
    from sqlalchemy.orm import selectinload
    from sqlalchemy import select
    from app.models.profiles import Profile
    from app.models.organizations import Organization

    profile = (
        await db.execute(select(Profile).where(Profile.id == member.profile_id))
    ).scalars().first()

    organization = (
        await db.execute(select(Organization).where(Organization.id == member.organization_id))
    ).scalars().first()

    return {
        "id": str(member.profile_id),
        "email": member.email,
        "member": {
            "id": str(member.id),
            "name": member.name,
            "email": member.email,
            "organization_id": str(member.organization_id),
            "default_role": member.default_role,
            "onboarding_completed": member.onboarding_completed,
        },
        "profile": {
            "id": str(profile.id) if profile else None,
            "first_name": profile.first_name if profile else None,
            "last_name": profile.last_name if profile else None,
            "email": profile.email if profile else None,
        },
        "organization": {
            "id": str(organization.id) if organization else None,
            "name": organization.name if organization else None,
            "onboarding_completed": organization.onboarding_completed if organization else None,
        } if organization else None,
    }
