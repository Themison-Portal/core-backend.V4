"""
Trial document routes — GET /, GET /{id}, POST /upload, PUT /{id}, DELETE /{id}
"""

from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.contracts.document import DocumentResponse, DocumentUpdate
from app.dependencies.auth import get_current_member
from app.dependencies.db import get_db
from app.dependencies.storage import get_storage_service
from app.models.documents import Document
from app.models.members import Member
from app.services.crud import CRUDBase
from app.services.storage.gcs_service import GCSStorageService

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
    file: UploadFile = File(...),
    trial_id: UUID = Form(...),
    document_name: str = Form(...),
    document_type: str = Form("other"),
    description: str = Form(""),
    member: Member = Depends(get_current_member),
    db: AsyncSession = Depends(get_db),
    storage: GCSStorageService = Depends(get_storage_service),
):
    settings = get_settings()
    bucket = settings.gcs_bucket_trial_documents

    file_data = await file.read()
    folder = f"trials/{trial_id}"

    upload_result = storage.upload_file(
        bucket_name=bucket,
        file_data=file_data,
        filename=file.filename or "document",
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
    storage: GCSStorageService = Depends(get_storage_service),
):
    crud = CRUDBase(Document, db)
    doc = await crud.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Delete from storage if URL looks like a GCS path
    settings = get_settings()
    if doc.document_url and settings.gcs_bucket_trial_documents:
        storage.delete_file(settings.gcs_bucket_trial_documents, doc.document_url)

    await crud.delete(document_id)
