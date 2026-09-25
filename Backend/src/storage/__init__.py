"""Stockage des données patients sur le serveur local."""

from src.storage.base import StorageBackend, StorageError
from src.storage.factory import get_storage

__all__ = ["StorageBackend", "StorageError", "get_storage"]
