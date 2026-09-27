"""Storage on the server file system (local mode)."""

from __future__ import annotations

import shutil
from pathlib import Path

from loguru import logger

from src.storage.base import StorageBackend, StorageError


class LocalStorage(StorageBackend):
    """
    Arborescence :
        <root>/patients/<ID>/input/   FASTQ, VCF
        <root>/patients/<ID>/output/  BAM, VCF, rapports JSON
    """

    name = "local"

    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def _resolve(self, uri: str) -> Path:
        if not uri:
            raise StorageError("Empty path")
        if uri.startswith("s3://"):
            raise StorageError(
                f"S3 URI not supported (local storage only): {uri}. "
                f"Place the file under {self.root}/patients/<ID>/input/"
            )
        if uri.startswith("file://"):
            uri = uri[len("file://"):]
        p = Path(uri)
        if not p.is_absolute():
            p = self.root / p
        return p.resolve()

    def _is_under_root(self, p: Path) -> bool:
        try:
            p.relative_to(self.root)
            return True
        except ValueError:
            return False

    def validate_input_uri(self, uri: str) -> str:
        uri = (uri or "").strip()
        try:
            p = self._resolve(uri)
        except StorageError as e:
            raise ValueError(str(e)) from e
        # Health data: any path outside the root is refused (no ../ nor /etc/…)
        if not self._is_under_root(p):
            raise ValueError(f"Path outside LOCAL_DATA_ROOT ({self.root}): {uri}")
        if not p.is_file():
            raise ValueError(f"File not found on the server: {p}")
        return str(p)

    def is_managed(self, uri: str) -> bool:
        try:
            return self._is_under_root(self._resolve(uri))
        except StorageError:
            return False

    def exists(self, uri: str) -> bool:
        try:
            p = self._resolve(uri)
        except StorageError:
            return False
        return p.is_file() and p.stat().st_size > 0

    def fetch(self, uri: str, dest: Path) -> str:
        # No transfer: the file is read in place.
        p = self._resolve(uri)
        if not p.is_file():
            raise StorageError(f"File not found: {p}")
        return str(p)

    def put(self, local_path: str, key: str, area: str = "output", move: bool = False) -> str:
        src = Path(local_path).resolve()
        if not src.is_file():
            raise StorageError(f"Local file not found: {local_path}")
        dest = self._resolve(key)
        if not self._is_under_root(dest):
            raise StorageError(f"Destination hors de LOCAL_DATA_ROOT : {dest}")
        if src == dest:
            return str(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if move:
            shutil.move(str(src), str(dest))
        else:
            shutil.copy2(src, dest)
        logger.info(f"Stockage local : {src} → {dest}")
        return str(dest)

    def key_for(self, patient_id: str, area: str, filename: str) -> str:
        sub = "input" if area == "input" else "output"
        return f"patients/{patient_id}/{sub}/{filename}"

    def local_patient_dir(self, patient_id: str, area: str) -> Path | None:
        d = self._resolve(self.key_for(patient_id, area, "x")).parent
        d.mkdir(parents=True, exist_ok=True)
        return d
