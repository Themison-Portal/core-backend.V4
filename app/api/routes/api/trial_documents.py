"""
Trial document routes — GET /, GET /{id}, POST /upload, PUT /{id}, DELETE /{id}
"""

import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts.document import DocumentResponse, DocumentUpdate
from app.dependencies.auth import get_current_member
from app.dependencies.db import get_db
from app.dependencies.jobs import get_job_status_service
from app.dependencies.storage import get_storage_service
from app.models.documents import Document
from app.models.members import Member
from app.models.trials import Trial
from app.services.crud import CRUDBase
from app.services.jobs.job_status_service import JobStatusService
from app.services.storage.base import StorageService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_model=List[DocumentResponse])
async def list_trial_documents(
    trial_id: Optional[UUID] = None,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    crud = CRUDBase(Document, db)
    filters = {}
    if trial_id:
        filters["trial_id"] = trial_id
    return await crud.get_multi(filters=filters)


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_trial_document(
    document_id: UUID,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    crud = CRUDBase(Document, db)
    doc = await crud.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.post("/upload", response_model=DocumentResponse, status_code=201)
async def upload_trial_document(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    trial_id: UUID = Form(...),
    document_name: str = Form(...),
    document_type: str = Form("other"),
    description: str = Form(""),
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
    job_service: JobStatusService = Depends(get_job_status_service),
):
    crud_trial = CRUDBase(Trial, db)
    trial = await crud_trial.get(trial_id)
    if not trial:
        raise HTTPException(status_code=404, detail="Trial not found")

    settings = get_settings()
    bucket = settings.gcs_bucket_trial_documents

    import uuid
    file_data = await file.read()
    folder = f"trials/{trial_id}"
    # Ensure a unique filename to avoid duplicate URL constraint
    unique_suffix = uuid.uuid4().hex[:8]
    base_name = file.filename or "document"
    # Preserve extension if present
    if "." in base_name:
        name_part, ext = base_name.rsplit(".", 1)
        unique_filename = f"{name_part}_{unique_suffix}.{ext}"
    else:
        unique_filename = f"{base_name}_{unique_suffix}"
    upload_result = storage.upload_file(
        bucket_name=bucket,
        file_data=file_data,
        filename=unique_filename,
        content_type=file.content_type or "application/octet-stream",
        folder=folder,
    )

    doc = Document(
        document_name=document_name,
        document_type=document_type,
        document_url=upload_result["path"],
        trial_id=trial_id,
        uploaded_by=member.id,
        status="active",
        file_size=upload_result["file_size"],
        mime_type=file.content_type,
        description=description,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    # Auto-trigger RAG ingestion in background so the document becomes queryable.
    # Failures here must not break the upload — the row is already persisted.
    try:
        from app.api.routes.upload import _run_ingestion_task

        job_id = await job_service.create_job(doc.id)
        redis_client = request.app.state.redis_client
        background_tasks.add_task(
            _run_ingestion_task,
            job_id=job_id,
            document_url=doc.document_url,
            document_id=doc.id,
            chunk_size=750,
            redis_client=redis_client,
            use_grpc=settings.use_grpc_rag,
            grpc_address=settings.rag_service_address,
        )
        logger.info(f"Queued RAG ingestion job {job_id} for document {doc.id}")
    except Exception as e:
        logger.exception(f"Failed to queue RAG ingestion for document {doc.id}: {e}")

    return doc


@router.put("/{document_id}", response_model=DocumentResponse)
async def update_trial_document(
    document_id: UUID,
    payload: DocumentUpdate,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
):
    crud = CRUDBase(Document, db)
    doc = await crud.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    updated = await crud.update(document_id, payload.model_dump(exclude_unset=True))
    return updated


@router.delete("/{document_id}", status_code=204)
async def delete_trial_document(
    document_id: UUID,
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    crud = CRUDBase(Document, db)
    doc = await crud.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete from storage
    settings = get_settings()
    if doc.document_url:
        storage.delete_file(settings.gcs_bucket_trial_documents, doc.document_url)

    await crud.delete(document_id)
