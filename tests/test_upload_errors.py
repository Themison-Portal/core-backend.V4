"""
Error handling tests for the /upload/upload-pdf endpoint.
Tests that different exception types map to correct HTTP status codes.
"""

import pytest
from datetime import datetime
from uuid import UUID
from unittest.mock import MagicMock, AsyncMock

from fastapi.testclient import TestClient

from app.main import app
from app.dependencies.rag import get_rag_ingestion_service


class TestUploadErrorHandling:
    """Test error handling in upload endpoint."""

    def test_upload_value_error_returns_400(
        self, client: TestClient, api_key: str
    ):
        """Test that ValueError from service returns 400 status."""
        if not api_key:
            pytest.skip("UPLOAD_API_KEY not configured")

        # Create a mock service that raises ValueError
        mock_service = MagicMock()
        mock_service.ingest_pdf = AsyncMock(
            side_effect=ValueError("Document not found in database")
        )

        app.dependency_overrides[get_rag_ingestion_service] = lambda: mock_service

        try:
            response = client.post(
                "/upload/upload-pdf",
                json={
                    "document_url": "https://example.com/test.pdf",
                    "document_id": "00000000-0000-0000-0000-000000000001",
                },
                headers={"X-API-KEY": api_key}
            )

            assert response.status_code == 400
            detail = response.json().get("detail", "")
            assert "Document not found" in detail

        finally:
            app.dependency_overrides.pop(get_rag_ingestion_service, None)

    def test_upload_runtime_error_returns_500(
        self, client: TestClient, api_key: str
    ):
        """Test that RuntimeError from service returns 500 status."""
        if not api_key:
            pytest.skip("UPLOAD_API_KEY not configured")

        mock_service = MagicMock()
        mock_service.ingest_pdf = AsyncMock(
            side_effect=RuntimeError("Database connection failed")
        )

        app.dependency_overrides[get_rag_ingestion_service] = lambda: mock_service

        try:
            response = client.post(
                "/upload/upload-pdf",
                json={
                    "document_url": "https://example.com/test.pdf",
                    "document_id": "00000000-0000-0000-0000-000000000001",
                },
                headers={"X-API-KEY": api_key}
            )

            assert response.status_code == 500
            detail = response.json().get("detail", "")
            assert "Database connection failed" in detail

        finally:
            app.dependency_overrides.pop(get_rag_ingestion_service, None)

    def test_upload_generic_exception_returns_500(
        self, client: TestClient, api_key: str
    ):
        """Test that generic Exception returns 500 with sanitized message."""
        if not api_key:
            pytest.skip("UPLOAD_API_KEY not configured")

        mock_service = MagicMock()
        mock_service.ingest_pdf = AsyncMock(
            side_effect=Exception("Unexpected internal error")
        )

        app.dependency_overrides[get_rag_ingestion_service] = lambda: mock_service

        try:
            response = client.post(
                "/upload/upload-pdf",
                json={
                    "document_url": "https://example.com/test.pdf",
                    "document_id": "00000000-0000-0000-0000-000000000001",
                },
                headers={"X-API-KEY": api_key}
            )

            assert response.status_code == 500
            detail = response.json().get("detail", "")
            assert "Internal server error" in detail

        finally:
            app.dependency_overrides.pop(get_rag_ingestion_service, None)

    def test_upload_pdf_download_failure(
        self, client: TestClient, api_key: str
    ):
        """Test that PDF download failure returns 500."""
        if not api_key:
            pytest.skip("UPLOAD_API_KEY not configured")

        mock_service = MagicMock()
        mock_service.ingest_pdf = AsyncMock(
            side_effect=RuntimeError("PDF ingestion failed: HTTP 404 Not Found")
        )

        app.dependency_overrides[get_rag_ingestion_service] = lambda: mock_service

        try:
            response = client.post(
                "/upload/upload-pdf",
                json={
                    "document_url": "https://example.com/nonexistent.pdf",
                    "document_id": "00000000-0000-0000-0000-000000000001",
                },
                headers={"X-API-KEY": api_key}
            )

            assert response.status_code == 500
            detail = response.json().get("detail", "")
            assert "PDF ingestion failed" in detail or "404" in detail

        finally:
            app.dependency_overrides.pop(get_rag_ingestion_service, None)

    def test_upload_embedding_failure(
        self, client: TestClient, api_key: str
    ):
        """Test that embedding generation failure returns 500."""
        if not api_key:
            pytest.skip("UPLOAD_API_KEY not configured")

        mock_service = MagicMock()
        mock_service.ingest_pdf = AsyncMock(
            side_effect=RuntimeError("PDF ingestion failed: OpenAI API rate limit exceeded")
        )

        app.dependency_overrides[get_rag_ingestion_service] = lambda: mock_service

        try:
            response = client.post(
                "/upload/upload-pdf",
                json={
                    "document_url": "https://example.com/test.pdf",
                    "document_id": "00000000-0000-0000-0000-000000000001",
                },
                headers={"X-API-KEY": api_key}
            )

            assert response.status_code == 500
            detail = response.json().get("detail", "")
            assert "PDF ingestion failed" in detail or "OpenAI" in detail

        finally:
            app.dependency_overrides.pop(get_rag_ingestion_service, None)

    def test_upload_database_insert_failure(
        self, client: TestClient, api_key: str
    ):
        """Test that database insert failure returns 500."""
        if not api_key:
            pytest.skip("UPLOAD_API_KEY not configured")

        mock_service = MagicMock()
        mock_service.ingest_pdf = AsyncMock(
            side_effect=RuntimeError("PDF ingestion failed: Failed to insert chunks")
        )

        app.dependency_overrides[get_rag_ingestion_service] = lambda: mock_service

        try:
            response = client.post(
                "/upload/upload-pdf",
                json={
                    "document_url": "https://example.com/test.pdf",
                    "document_id": "00000000-0000-0000-0000-000000000001",
                },
                headers={"X-API-KEY": api_key}
            )

            assert response.status_code == 500
            detail = response.json().get("detail", "")
            assert "Failed to insert chunks" in detail or "PDF ingestion failed" in detail

        finally:
            app.dependency_overrides.pop(get_rag_ingestion_service, None)


class TestUploadErrorMessages:
    """Test that error messages are properly propagated."""

    def test_value_error_message_preserved(
        self, client: TestClient, api_key: str
    ):
        """Test that ValueError message is preserved in response."""
        if not api_key:
            pytest.skip("UPLOAD_API_KEY not configured")

        error_message = "Invalid document format: expected PDF header"
        mock_service = MagicMock()
        mock_service.ingest_pdf = AsyncMock(
            side_effect=ValueError(error_message)
        )

        app.dependency_overrides[get_rag_ingestion_service] = lambda: mock_service

        try:
            response = client.post(
                "/upload/upload-pdf",
                json={
                    "document_url": "https://example.com/test.pdf",
                    "document_id": "00000000-0000-0000-0000-000000000001",
                },
                headers={"X-API-KEY": api_key}
            )

            assert response.status_code == 400
            assert error_message in response.json().get("detail", "")

        finally:
            app.dependency_overrides.pop(get_rag_ingestion_service, None)

    def test_runtime_error_message_preserved(
        self, client: TestClient, api_key: str
    ):
        """Test that RuntimeError message is preserved in response."""
        if not api_key:
            pytest.skip("UPLOAD_API_KEY not configured")

        error_message = "Connection timeout to embedding service"
        mock_service = MagicMock()
        mock_service.ingest_pdf = AsyncMock(
            side_effect=RuntimeError(error_message)
        )

        app.dependency_overrides[get_rag_ingestion_service] = lambda: mock_service

        try:
            response = client.post(
                "/upload/upload-pdf",
                json={
                    "document_url": "https://example.com/test.pdf",
                    "document_id": "00000000-0000-0000-0000-000000000001",
                },
                headers={"X-API-KEY": api_key}
            )

            assert response.status_code == 500
            assert error_message in response.json().get("detail", "")

        finally:
            app.dependency_overrides.pop(get_rag_ingestion_service, None)

    def test_generic_exception_includes_error_in_detail(
        self, client: TestClient, api_key: str
    ):
        """Test that generic exception message is included in detail."""
        if not api_key:
            pytest.skip("UPLOAD_API_KEY not configured")

        error_message = "Something went wrong"
        mock_service = MagicMock()
        mock_service.ingest_pdf = AsyncMock(
            side_effect=Exception(error_message)
        )

        app.dependency_overrides[get_rag_ingestion_service] = lambda: mock_service

        try:
            response = client.post(
                "/upload/upload-pdf",
                json={
                    "document_url": "https://example.com/test.pdf",
                    "document_id": "00000000-0000-0000-0000-000000000001",
                },
                headers={"X-API-KEY": api_key}
            )

            assert response.status_code == 500
            detail = response.json().get("detail", "")
            # The endpoint wraps generic exceptions with "Internal server error: {msg}"
            assert "Internal server error" in detail
            assert error_message in detail

        finally:
            app.dependency_overrides.pop(get_rag_ingestion_service, None)
