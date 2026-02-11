"""
FastAPI dependency for the GCS storage service (singleton).
"""

from functools import lru_cache

from app.services.storage.gcs_service import GCSStorageService


@lru_cache()
def _singleton() -> GCSStorageService:
    return GCSStorageService()


def get_storage_service() -> GCSStorageService:
    return _singleton()
