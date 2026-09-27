"""Common interface of the storage backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class StorageError(Exception):
    """Storage access error (missing file, invalid URI, etc.)."""


class StorageBackend(ABC):
    """Agents handle URIs: absolute paths under LOCAL_DATA_ROOT (/data/germlineiq/patients/…).

    Interface kept to isolate the agents from the file system (tests, future object storage).
    """

    name: str = "abstract"

    @abstractmethod
    def validate_input_uri(self, uri: str) -> str:
        """Validates a user-provided URI; returns its normalised form or raises ValueError."""

    @abstractmethod
    def is_managed(self, uri: str) -> bool:
        """True if the URI is already in the storage (no need to copy it)."""

    @abstractmethod
    def exists(self, uri: str) -> bool:
        ...

    @abstractmethod
    def fetch(self, uri: str, dest: Path) -> str:
        """Returns the local path to read (the file is read in place)."""

    @abstractmethod
    def put(self, local_path: str, key: str, area: str = "output", move: bool = False) -> str:
        """Stores a local file under `key`; returns its URI. area ∈ {input, output}."""

    @abstractmethod
    def key_for(self, patient_id: str, area: str, filename: str) -> str:
        """Storage key of a patient file. area ∈ {input, output, report}."""

    def local_patient_dir(self, patient_id: str, area: str) -> Path | None:
        """Local directory where outputs are written directly."""
        return None
