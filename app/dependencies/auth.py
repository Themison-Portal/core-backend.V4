"""
Authentication dependencies — Auth0 JWT verification.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.auth0 import verify_auth0_token
from app.dependencies.db import get_db
from app.models.members import Member
from app.models.profiles import Profile

logger = logging.getLogger(__name__)

# Make HTTPBearer optional when auth is disabled
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Dict[str, Any]:
    """
    Verify Auth0 JWT and return basic user info.

    Returns dict with ``id`` (profile UUID), ``email``, ``auth0_sub``.

    If AUTH_DISABLED=true in .env, returns a mock test user.
    """
    settings = get_settings()

    # Bypass Auth0 when disabled (for testing)
    if settings.auth_disabled:
        logger.warning("Auth0 disabled - using mock test user")
        return {
            "id": "test-user-id",
            "email": "test@themison.com",
            "auth0_sub": "auth0|test-user-id",
        }

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    try:
        payload = await verify_auth0_token(token)
    except ValueError as e:
        logger.error("Auth0 token verification failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "id": payload.get("sub"),
        "email": payload.get("email") or payload.get(
            "https://themison.com/email", ""
        ),
        "auth0_sub": payload.get("sub"),
    }


async def get_current_member(
    user: Dict[str, Any] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Member:
    """
    Resolve the Auth0 user to a Member record (carries organization_id for scoping).

    Looks up profiles by email, then members by profile_id.
    Raises 403 if no member record exists.

    If AUTH_DISABLED=true, returns first member in database (for testing).
    """
    settings = get_settings()

    # Bypass member lookup when auth is disabled (for testing)
    if settings.auth_disabled:
        logger.warning("Auth0 disabled - returning first available member")
        result = await db.execute(select(Member).limit(1))
        member = result.scalars().first()
        if not member:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No members in database. Create test data first.",
            )
        return member

    email = user.get("email", "")

    # Find profile by email
    result = await db.execute(
        select(Profile).where(Profile.email == email)
    )
    profile = result.scalars().first()

    if not profile:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No profile found for this account",
        )

    # Find member by profile_id
    result = await db.execute(
        select(Member).where(Member.profile_id == profile.id)
    )
    member = result.scalars().first()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No organization membership found for this account",
        )

    return member


async def get_current_user_id(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> str:
    """Return just the current user's Auth0 sub claim."""
    return current_user["id"]
