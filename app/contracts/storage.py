"""
Contracts for storage operations.
"""

from typing import Optional

from .base import BaseContract


class UploadResponse(BaseContract):
    path: str
    signed_url: str
    file_size: int


class DownloadUrlResponse(BaseContract):
    signed_url: str
