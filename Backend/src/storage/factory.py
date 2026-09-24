"""Stockage des données patients sur le disque du serveur (LOCAL_DATA_ROOT)."""

from __future__ import annotations

from typing import Dict

from config.settings import paths
from src.storage.base import StorageBackend

_instances: Dict[str, StorageBackend] = {}


def get_storage() -> StorageBackend:
    from src.storage.local import LocalStorage

    root = paths().data_root
    key = str(root)
    if key not in _instances:
        _instances[key] = LocalStorage(root)
    return _instances[key]
