"""
Storage routes — upload, download, delete files via GCS.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status

from app.config import get_settings
from app.contracts.storage import DownloadUrlResponse, UploadResponse
from app.dependencies.auth import get_current_member
from app.dependencies.storage import get_storage_service
from app.models.members import Member
from app.services.storage.gcs_service import GCSStorageService

router = APIRouter()


@router.post("/upload", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    bucket: str | None = None,
    folder: str | None = None,
    member: Member = Depends(get_current_member),
    storage: GCSStorageService = Depends(get_storage_service),
):
    """Upload a file to GCS and return its path + signed URL."""
    settings = get_settings()
    bucket_name = bucket or settings.gcs_bucket_trial_documents

    if not bucket_name:
        raise HTTPException(status_code=500, detail="No GCS bucket configured")

    data = await file.read()
    result = storage.upload_file(
        bucket_name=bucket_name,
        file_data=data,
        filename=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
        folder=folder,
    )
    return result


@router.get("/download/{document_id}", response_model=DownloadUrlResponse)
async def download_file(
    document_id: UUID,
    member: Member = Depends(get_current_member),
    storage: GCSStorageService = Depends(get_storage_service),
):
    """Return a signed download URL for a given document_id.

    For now, this looks up the trial_documents table by ID and generates a
    signed URL from the stored ``document_url`` (which contains the blob path).
    """
    from sqlalchemy import select
    from app.dependencies.db import get_db
    from app.models.documents import Document

    # We need a db session — obtain manually since this is a simple lookup
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Full download implementation pending document path resolution",
    )


@router.delete("/{path:path}", status_code=204)
async def delete_file(
    path: str,
    bucket: str | None = None,
    member: Member = Depends(get_current_member),
    storage: GCSStorageService = Depends(get_storage_service),
):
    """Delete a file from GCS by its blob path."""
    settings = get_settings()
    bucket_name = bucket or settings.gcs_bucket_trial_documents

    if not bucket_name:
        raise HTTPException(status_code=500, detail="No GCS bucket configured")

    storage.delete_file(bucket_name, path)
