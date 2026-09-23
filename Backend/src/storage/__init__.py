"""Abstraction du stockage des données patients (local ou S3)."""

from src.storage.base import StorageBackend, StorageError
from src.storage.factory import get_storage

__all__ = ["StorageBackend", "StorageError", "get_storage"]
