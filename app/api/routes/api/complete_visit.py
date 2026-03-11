"""
Patient visit - complete visit endpoint
POST: /trials/{trial_id}/patients/{patient_id}/visits/{visit_id}/complete
"""

from datetime import datetime, date, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies.auth import get_current_member
from app.dependencies.db import get_db
from app.models.members import Member
from app.models.patient_visits import PatientVisit
from app.models.visit_activities import VisitActivity
from app.utils.permissions import is_critical_trial_role  # PI/CRC roles checker

router = APIRouter()


@router.post(
    "/trials/{trial_id}/patients/{patient_id}/visits/{visit_id}/complete",
    response_model=PatientVisit,
)
async def complete_visit(
    trial_id: str,
    patient_id: str,
    visit_id: str,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Complete a patient visit
    - Only PI/CRC can complete
    - All activities must be completed or not_applicable
    """
    # Check permission
    if not is_critical_trial_role(member.trial_role):
        raise HTTPException(
            status_code=403, detail="Only PI and CRC can complete visits"
        )

    # Fetch visit
    visit_stmt = select(PatientVisit).where(
        PatientVisit.id == visit_id,
        PatientVisit.patient_id == patient_id,
        PatientVisit.trial_id == trial_id,
    )
    visit_result = await db.execute(visit_stmt)
    visit: PatientVisit = visit_result.scalars().first()

    if not visit:
        raise HTTPException(status_code=404, detail="Visit not found")

    if visit.status == "completed":
        raise HTTPException(status_code=400, detail="Visit is already completed")

    # Fetch all activities for this visit
    activities_stmt = select(VisitActivity).where(VisitActivity.visit_id == visit_id)
    activities_result = await db.execute(activities_stmt)
    activities: List[VisitActivity] = activities_result.scalars().all()

    # Check pending activities
    pending_activities = [a.activity_name for a in activities if a.status == "pending"]
    if pending_activities:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot complete visit with pending activities: {pending_activities}",
        )

    # Complete the visit
    visit.status = "completed"
    visit.actual_date = date.today()
    visit.updated_at = datetime.now(timezone.utc)

    db.add(visit)
    await db.commit()
    await db.refresh(visit)

    return visit
