"""
Google Cloud Storage service for file upload, download, and deletion.
"""

import logging
from datetime import timedelta
from typing import Optional

from google.cloud import storage as gcs
from google.oauth2 import service_account

from app.config import get_settings

logger = logging.getLogger(__name__)


class GCSStorageService:
    """Thin wrapper around google-cloud-storage for Themison file operations."""

    def __init__(self) -> None:
        settings = get_settings()
        creds_path = settings.gcs_credentials_path

        if creds_path:
            credentials = service_account.Credentials.from_service_account_file(creds_path)
            self._client = gcs.Client(
                project=settings.gcs_project_id or None,
                credentials=credentials,
            )
        else:
            # Relies on Application Default Credentials (e.g. GCE metadata, GOOGLE_APPLICATION_CREDENTIALS)
            self._client = gcs.Client(project=settings.gcs_project_id or None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upload_file(
        self,
        bucket_name: str,
        file_data: bytes,
        filename: str,
        content_type: str = "application/octet-stream",
        folder: Optional[str] = None,
    ) -> dict:
        """
        Upload bytes to GCS and return ``{path, signed_url, file_size}``.
        """
        blob_path = f"{folder}/{filename}" if folder else filename

        bucket = self._client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        blob.upload_from_string(file_data, content_type=content_type)

        signed_url = self.get_signed_url(bucket_name, blob_path)

        return {
            "path": blob_path,
            "signed_url": signed_url,
            "file_size": len(file_data),
        }

    def get_signed_url(
        self,
        bucket_name: str,
        blob_path: str,
        expiration_hours: int = 1,
    ) -> str:
        """Return a signed download URL valid for ``expiration_hours``."""
        bucket = self._client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(hours=expiration_hours),
            method="GET",
        )

    def delete_file(self, bucket_name: str, blob_path: str) -> None:
        """Delete a blob from GCS. Silently ignores missing files."""
        bucket = self._client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        try:
            blob.delete()
        except Exception:
            logger.warning("Failed to delete %s/%s (may already be deleted)", bucket_name, blob_path)
