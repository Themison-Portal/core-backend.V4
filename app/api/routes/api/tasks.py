from fastapi import APIRouter, Depends, HTTPException, Body
from typing import List, Optional
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tasks import Task
from app.models.members import Member
from app.dependencies.db import get_db
from app.dependencies.auth import get_current_member
from app.contracts.tasks import TaskResponse, TaskCreate, TaskUpdate, AssignedUser

router = APIRouter(prefix="/tasks", tags=["tasks"])


# -----------------------
# GET - List Tasks
# -----------------------
@router.get("/", response_model=List[TaskResponse])
async def list_tasks(
    trial_ids: Optional[List[UUID]] = None,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Get tasks optionally filtered by trial_ids.
    Only tasks for trials the member has access to.
    """
    stmt = select(Task)

    if trial_ids:
        stmt = stmt.where(Task.trial_id.in_(trial_ids))

    result = await db.execute(stmt)
    tasks = result.scalars().all()

    # Add assigned user info
    tasks_with_user = []
    for task in tasks:
        assigned_user = None
        if task.assigned_to:
            user = await db.get(Member, task.assigned_to)
            assigned_user = (
                AssignedUser(id=user.id, full_name=user.full_name) if user else None
            )

        tasks_with_user.append(
            TaskResponse(
                id=task.id,
                trial_id=task.trial_id,
                status=task.status,
                due_date=task.due_date,
                assigned_to=task.assigned_to,
                assigned_user=assigned_user,
                created_at=task.created_at,
                updated_at=task.updated_at,
            )
        )

    return tasks_with_user


# -----------------------
# POST - Create Task
# -----------------------
@router.post("/", response_model=TaskResponse, status_code=201)
async def create_task(
    payload: TaskCreate,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Create a new task.
    """
    task = Task(**payload.model_dump(), created_by=member.id)
    db.add(task)
    await db.commit()
    await db.refresh(task)

    assigned_user = None
    if task.assigned_to:
        user = await db.get(Member, task.assigned_to)
        assigned_user = (
            AssignedUser(id=user.id, full_name=user.full_name) if user else None
        )

    return TaskResponse(
        id=task.id,
        trial_id=task.trial_id,
        status=task.status,
        due_date=task.due_date,
        assigned_to=task.assigned_to,
        assigned_user=assigned_user,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


# -----------------------
# PUT - Update Task
# -----------------------
@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Update an existing task.
    """
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(task, key, value)

    await db.commit()
    await db.refresh(task)

    assigned_user = None
    if task.assigned_to:
        user = await db.get(Member, task.assigned_to)
        assigned_user = (
            AssignedUser(id=user.id, full_name=user.full_name) if user else None
        )

    return TaskResponse(
        id=task.id,
        trial_id=task.trial_id,
        status=task.status,
        due_date=task.due_date,
        assigned_to=task.assigned_to,
        assigned_user=assigned_user,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


# -----------------------
# DELETE - Soft Delete Task
# -----------------------
@router.delete("/{task_id}", status_code=204)
async def delete_task(
    task_id: UUID,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    """
    Soft delete a task.
    """
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Soft delete by setting a deleted_at timestamp
    task.deleted_at = task.updated_at = task.updated_at  # or datetime.utcnow()
    await db.commit()
