"""Interface commune aux backends de stockage."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class StorageError(Exception):
    """Erreur d'accès au stockage (fichier absent, URI invalide, etc.)."""


class StorageBackend(ABC):
    """
    Les agents manipulent des « URIs » opaques :
    - mode aws   : s3://bucket/key
    - mode local : chemin absolu sous LOCAL_DATA_ROOT (/data/zaynb/patients/…)
    """

    name: str = "abstract"

    @abstractmethod
    def validate_input_uri(self, uri: str) -> str:
        """Valide une URI fournie par l'utilisateur ; retourne sa forme normalisée ou lève ValueError."""

    @abstractmethod
    def is_managed(self, uri: str) -> bool:
        """True si l'URI est déjà dans le stockage (pas besoin de l'y copier)."""

    @abstractmethod
    def exists(self, uri: str) -> bool:
        ...

    @abstractmethod
    def fetch(self, uri: str, dest: Path) -> str:
        """Rend le fichier disponible localement ; retourne le chemin local à lire."""

    @abstractmethod
    def put(self, local_path: str, key: str, area: str = "output", move: bool = False) -> str:
        """Stocke un fichier local sous `key` ; retourne son URI. area ∈ {input, output}."""

    @abstractmethod
    def key_for(self, patient_id: str, area: str, filename: str) -> str:
        """Clé de stockage d'un fichier patient. area ∈ {input, output, report}."""

    def local_patient_dir(self, patient_id: str, area: str) -> Path | None:
        """Répertoire local où écrire directement les sorties (None si stockage distant)."""
        return None
