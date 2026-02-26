"""
Upload routes - PDF ingestion via local service or gRPC.
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.contracts.document import UploadPdfResponse
from app.config import get_settings
from app.dependencies.rag import get_rag_ingestion_service
from app.services.doclingRag.rag_ingestion_service import RagIngestionService

logger = logging.getLogger(__name__)
router = APIRouter()


class UploadDocumentRequest(BaseModel):
    """
    Upload document request
    """
    document_url: str
    document_id: UUID
    chunk_size: Optional[int] = 750


async def _ingest_via_grpc(request: UploadDocumentRequest) -> dict:
    """
    Ingest PDF using gRPC RAG Service.
    """
    from app.clients.rag_client import get_rag_client

    client = get_rag_client()
    result = None

    async for progress in client.ingest_pdf(
        document_url=request.document_url,
        document_id=request.document_id,
        chunk_size=request.chunk_size or 750,
    ):
        logger.info(f"[gRPC] Ingest progress: {progress['stage']} - {progress['message']}")
        if progress.get("result"):
            result = progress["result"]

    if not result:
        raise RuntimeError("gRPC ingestion did not return a result")

    if not result.get("success"):
        raise RuntimeError(result.get("error", "Unknown error"))

    return result


async def _ingest_via_local(
    request: UploadDocumentRequest,
    rag_service: RagIngestionService,
) -> dict:
    """
    Ingest PDF using local RAG service.
    """
    return await rag_service.ingest_pdf(
        document_url=request.document_url,
        document_id=request.document_id,
    )


@router.post("/upload-pdf", response_model=UploadPdfResponse)
async def upload_pdf_document(
    request: UploadDocumentRequest,
    rag_service: RagIngestionService = Depends(get_rag_ingestion_service),
    x_api_key: str = Header(...),
):
    """
    Upload a PDF document.

    Uses gRPC RAG Service if USE_GRPC_RAG=true, otherwise uses local service.
    """
    settings = get_settings()

    # Validate API key
    if not settings.upload_api_key or x_api_key != settings.upload_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Validate file type
    if not request.document_url.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    try:
        logger.info(f"Processing document ID: {request.document_id}")

        # Route to gRPC or local service
        if settings.use_grpc_rag:
            logger.info(f"Using gRPC RAG Service at {settings.rag_service_address}")
            result = await _ingest_via_grpc(request)
        else:
            logger.info("Using local RAG service")
            result = await _ingest_via_local(request, rag_service)

        return result

    except ValueError as e:
        logger.error(f"Validation error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.error(f"Runtime error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
