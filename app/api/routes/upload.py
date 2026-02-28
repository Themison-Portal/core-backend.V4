"""
Upload routes - PDF ingestion via background task.
Returns immediately with job ID, client polls for status.
"""
import asyncio
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import BaseModel

from app.config import get_settings
from app.contracts.document import UploadJobResponse, JobStatusResponse
from app.dependencies.jobs import get_job_status_service
from app.services.jobs.job_status_service import JobStatusService, JobStatus

logger = logging.getLogger(__name__)
router = APIRouter()


class UploadDocumentRequest(BaseModel):
    """Upload document request."""
    document_url: str
    document_id: UUID
    chunk_size: Optional[int] = 750


async def _run_ingestion_task(
    job_id: str,
    document_url: str,
    document_id: UUID,
    chunk_size: int,
    redis_client,
    use_grpc: bool,
    grpc_address: str,
):
    """
    Background task for PDF ingestion.
    Creates its own database session since request session is closed.
    """
    from app.services.jobs.job_status_service import JobStatusService
    from app.services.cache.rag_cache_service import RagCacheService
    from app.services.cache.semantic_cache_service import SemanticCacheService
    from app.services.doclingRag.rag_ingestion_service import RagIngestionService

    job_service = JobStatusService(redis_client)

    try:
        if use_grpc:
            # gRPC path - stream progress from RAG service
            await _ingest_via_grpc(
                job_id=job_id,
                document_url=document_url,
                document_id=document_id,
                chunk_size=chunk_size,
                job_service=job_service,
                grpc_address=grpc_address,
            )
        else:
            # Local path - run ingestion with progress updates
            await _ingest_via_local(
                job_id=job_id,
                document_url=document_url,
                document_id=document_id,
                chunk_size=chunk_size,
                job_service=job_service,
                redis_client=redis_client,
            )

    except Exception as e:
        logger.exception(f"Ingestion task failed for job {job_id}")
        await job_service.fail_job(job_id, str(e))


async def _ingest_via_grpc(
    job_id: str,
    document_url: str,
    document_id: UUID,
    chunk_size: int,
    job_service: JobStatusService,
    grpc_address: str,
):
    """Ingest PDF using gRPC RAG Service with progress streaming."""
    from app.clients.rag_client import RagClient

    client = RagClient(grpc_address)
    result = None

    async for progress in client.ingest_pdf(
        document_url=document_url,
        document_id=document_id,
        chunk_size=chunk_size,
    ):
        # Map gRPC progress to job status
        stage = progress.get("stage", "processing")
        percent = progress.get("progress_percent", 0)
        message = progress.get("message", "")

        await job_service.update_progress(
            job_id=job_id,
            stage=stage,
            progress_percent=percent,
            message=message,
        )

        if progress.get("result"):
            result = progress["result"]

    if not result:
        raise RuntimeError("gRPC ingestion did not return a result")

    if not result.get("success"):
        raise RuntimeError(result.get("error", "Unknown error"))

    await job_service.complete_job(job_id, result)


async def _ingest_via_local(
    job_id: str,
    document_url: str,
    document_id: UUID,
    chunk_size: int,
    job_service: JobStatusService,
    redis_client,
):
    """Ingest PDF using local RAG service with progress updates."""
    from app.db.session import async_session
    from app.services.cache.rag_cache_service import RagCacheService
    from app.services.cache.semantic_cache_service import SemanticCacheService
    from app.services.doclingRag.rag_ingestion_service import RagIngestionService

    # Create new database session for background task
    async with async_session() as db:
        cache_service = RagCacheService(redis_client)
        semantic_cache_service = SemanticCacheService(db)

        rag_service = RagIngestionService(
            db=db,
            cache_service=cache_service,
            semantic_cache_service=semantic_cache_service,
        )

        # Update progress: starting
        await job_service.update_progress(
            job_id=job_id,
            stage="invalidating",
            progress_percent=5,
            message="Invalidating existing cache...",
        )

        # Invalidate caches
        if cache_service:
            deleted_count = await cache_service.invalidate_document(document_id)
            if deleted_count > 0:
                logger.info(f"Invalidated {deleted_count} Redis cached entries")

        if semantic_cache_service:
            deleted_semantic = await semantic_cache_service.invalidate_document(document_id)
            if deleted_semantic > 0:
                logger.info(f"Invalidated {deleted_semantic} semantic cache entries")

        await job_service.update_progress(
            job_id=job_id,
            stage="preparing",
            progress_percent=10,
            message="Preparing document processor...",
        )

        # Delete existing chunks
        deleted_chunks = await rag_service._delete_existing_chunks(document_id)
        if deleted_chunks > 0:
            logger.info(f"Deleted {deleted_chunks} existing chunks")

        await job_service.update_progress(
            job_id=job_id,
            stage="downloading",
            progress_percent=15,
            message="Downloading PDF...",
        )

        # Load and parse PDF (this is the blocking part - run in thread)
        from app.services.utils.tokenizer import get_tokenizer
        from docling.chunking import HybridChunker
        from langchain_docling.loader import DoclingLoader, ExportType

        tokenizer = get_tokenizer()

        await job_service.update_progress(
            job_id=job_id,
            stage="parsing",
            progress_percent=25,
            message="Parsing PDF with Docling...",
        )

        # Run blocking DoclingLoader.load() in thread pool
        loader = DoclingLoader(
            file_path=document_url,
            export_type=ExportType.DOC_CHUNKS,
            chunker=HybridChunker(tokenizer=tokenizer, chunk_size=chunk_size),
        )
        docs = await asyncio.to_thread(loader.load)

        await job_service.update_progress(
            job_id=job_id,
            stage="chunking",
            progress_percent=50,
            message=f"Processing {len(docs)} chunks...",
        )

        texts = [doc.page_content for doc in docs]

        await job_service.update_progress(
            job_id=job_id,
            stage="embedding",
            progress_percent=60,
            message=f"Generating embeddings for {len(texts)} chunks...",
        )

        # Generate embeddings
        from app.core.openai import embedding_client
        chunk_embeddings = await embedding_client.aembed_documents(texts)

        await job_service.update_progress(
            job_id=job_id,
            stage="storing",
            progress_percent=85,
            message="Storing chunks in database...",
        )

        # Store chunks
        await rag_service._insert_docling_chunks(
            document_id=document_id,
            document_url=document_url,
            chunks=docs,
            embeddings=chunk_embeddings,
        )

        # Complete
        from datetime import datetime
        result = {
            "success": True,
            "document_id": str(document_id),
            "status": "ready",
            "chunks_count": len(docs),
            "created_at": datetime.now().isoformat(),
        }

        await job_service.complete_job(job_id, result)
        logger.info(f"Ingestion complete for document {document_id}")


@router.post("/upload-pdf", response_model=UploadJobResponse)
async def upload_pdf_document(
    request: Request,
    body: UploadDocumentRequest,
    background_tasks: BackgroundTasks,
    job_service: JobStatusService = Depends(get_job_status_service),
    x_api_key: str = Header(...),
):
    """
    Upload a PDF document for processing.

    Returns immediately with a job ID. Use GET /upload/status/{job_id} to poll for progress.
    """
    settings = get_settings()

    # Validate API key
    if not settings.upload_api_key or x_api_key != settings.upload_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    # Validate file type (handle URLs with query params like ?token=abc)
    from urllib.parse import urlparse
    url_path = urlparse(body.document_url).path.lower()
    if not url_path.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")

    # Create job
    job_id = await job_service.create_job(body.document_id)

    # Get redis client for background task
    redis_client = request.app.state.redis_client

    # Queue background task
    background_tasks.add_task(
        _run_ingestion_task,
        job_id=job_id,
        document_url=body.document_url,
        document_id=body.document_id,
        chunk_size=body.chunk_size or 750,
        redis_client=redis_client,
        use_grpc=settings.use_grpc_rag,
        grpc_address=settings.rag_service_address,
    )

    logger.info(f"Queued ingestion job {job_id} for document {body.document_id}")

    return UploadJobResponse(
        job_id=job_id,
        document_id=str(body.document_id),
        status="queued",
        message="Upload job queued for processing. Poll /upload/status/{job_id} for progress.",
    )


@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_upload_status(
    job_id: str,
    job_service: JobStatusService = Depends(get_job_status_service),
):
    """
    Get the status of an upload job.

    Poll this endpoint to track progress of PDF ingestion.
    """
    job = await job_service.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        job_id=job.job_id,
        document_id=job.document_id,
        status=job.status.value,
        progress_percent=job.progress_percent,
        current_stage=job.current_stage,
        message=job.message,
        result=job.result,
        error=job.error,
    )
