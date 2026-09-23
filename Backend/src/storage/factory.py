"""Sélection du backend de stockage selon DEPLOYMENT_MODE."""

from __future__ import annotations

from typing import Dict

from config.deployment import AWS, deployment_mode, local_data_root
from src.storage.base import StorageBackend

_instances: Dict[str, StorageBackend] = {}


def get_storage() -> StorageBackend:
    mode = deployment_mode()
    key = f"{mode}:{local_data_root()}"
    if key not in _instances:
        if mode == AWS:
            from src.storage.s3 import S3Storage

            _instances[key] = S3Storage()
        else:
            from src.storage.local import LocalStorage

            _instances[key] = LocalStorage(local_data_root())
    return _instances[key]
