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
