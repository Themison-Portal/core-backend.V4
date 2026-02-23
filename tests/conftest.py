"""
Pytest fixtures for backend tests.
"""

import os
import pytest
import pytest_asyncio
from typing import AsyncGenerator, Generator
from uuid import UUID

from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# Set test environment before importing app
os.environ.setdefault("AUTH_DISABLED", "true")

from app.main import app
from app.dependencies.db import get_db
from app.dependencies.auth import get_current_user, get_current_member
from app.config import get_settings


# --- Mock User for Testing ---
def mock_user_override():
    """Mock user when auth is disabled."""
    return {
        "id": "test-user-id",
        "email": "test@themison.com",
        "auth0_sub": "auth0|test-user-id",
    }


# --- Fixtures ---

@pytest.fixture(scope="session")
def settings():
    """Get application settings."""
    return get_settings()


@pytest.fixture(scope="session")
def database_url(settings):
    """Get database URL from settings."""
    return settings.database_url


@pytest.fixture(scope="session")
def api_key(settings):
    """Get upload API key from settings."""
    return settings.upload_api_key


@pytest.fixture(scope="function")
def client() -> Generator[TestClient, None, None]:
    """
    Synchronous test client for FastAPI.
    Auth is bypassed via AUTH_DISABLED=true.
    """
    app.dependency_overrides[get_current_user] = mock_user_override
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """
    Async test client for FastAPI.
    """
    app.dependency_overrides[get_current_user] = mock_user_override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def db_session(database_url) -> AsyncGenerator[AsyncSession, None]:
    """
    Create a database session for testing.
    """
    engine = create_async_engine(database_url, echo=False)
    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def test_document_id() -> str:
    """A test document UUID (you may need to create this in the database first)."""
    return "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def test_organization_id() -> str:
    """A test organization UUID."""
    return "00000000-0000-0000-0000-000000000001"


# --- Mock Fixtures for Upload Tests ---

@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """Fake PDF bytes for testing."""
    return b"%PDF-1.4 fake pdf content for testing"


@pytest.fixture
def mock_embedding_vectors() -> list:
    """Fake embedding vectors (1536-dim OpenAI embeddings)."""
    return [[0.1] * 1536 for _ in range(3)]


@pytest.fixture
def mock_docling_documents():
    """Fake Docling Document objects."""
    from langchain_core.documents import Document
    return [
        Document(
            page_content="This is chunk 1 content about clinical trials.",
            metadata={
                "dl_meta": {
                    "doc_items": [{"prov": [{"page_no": 1}]}],
                    "headings": ["Introduction"]
                }
            }
        ),
        Document(
            page_content="This is chunk 2 content about patient enrollment.",
            metadata={
                "dl_meta": {
                    "doc_items": [{"prov": [{"page_no": 1}]}],
                    "headings": ["Methods"]
                }
            }
        ),
        Document(
            page_content="This is chunk 3 content about adverse events.",
            metadata={
                "dl_meta": {
                    "doc_items": [{"prov": [{"page_no": 2}]}],
                    "headings": ["Results"]
                }
            }
        ),
    ]


@pytest.fixture
def mock_rag_ingestion_service():
    """
    Create a mock RagIngestionService that returns success without real operations.
    """
    from unittest.mock import AsyncMock, MagicMock
    from datetime import datetime
    from uuid import UUID

    mock_service = MagicMock()
    mock_service.ingest_pdf = AsyncMock(return_value={
        "success": True,
        "document_id": UUID("00000000-0000-0000-0000-000000000001"),
        "status": "ready",
        "chunks_count": 42,
        "created_at": datetime.now(),
    })
    return mock_service


@pytest.fixture
def mock_rag_cache_service():
    """Create a mock RagCacheService."""
    from unittest.mock import AsyncMock, MagicMock

    mock_service = MagicMock()
    mock_service.invalidate_document = AsyncMock(return_value=5)
    mock_service.get_embedding = AsyncMock(return_value=None)
    mock_service.set_embedding = AsyncMock(return_value=None)
    return mock_service


@pytest.fixture
def mock_semantic_cache_service():
    """Create a mock SemanticCacheService."""
    from unittest.mock import AsyncMock, MagicMock

    mock_service = MagicMock()
    mock_service.invalidate_document = AsyncMock(return_value=3)
    mock_service.lookup = AsyncMock(return_value=None)
    mock_service.store = AsyncMock(return_value=None)
    return mock_service


@pytest.fixture
def mock_embedding_client(mock_embedding_vectors):
    """Create a mock OpenAI embedding client."""
    from unittest.mock import AsyncMock, MagicMock

    mock_client = MagicMock()
    mock_client.aembed_documents = AsyncMock(return_value=mock_embedding_vectors)
    mock_client.aembed_query = AsyncMock(return_value=mock_embedding_vectors[0])
    return mock_client


@pytest.fixture
def mock_db_session():
    """Create a mock async database session."""
    from unittest.mock import AsyncMock, MagicMock

    mock_session = MagicMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.close = AsyncMock()

    # Mock execute to return a result with rowcount
    mock_result = MagicMock()
    mock_result.rowcount = 0
    mock_session.execute.return_value = mock_result

    return mock_session
