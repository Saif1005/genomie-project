"""Stockage S3 (mode aws) — enveloppe le S3Manager existant."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

from src.storage.base import StorageBackend, StorageError

_S3_URI_RE = re.compile(r"^s3://[a-z0-9.\-]+/.+", re.IGNORECASE)


def split_s3_uri(uri: str) -> Tuple[str, str]:
    bucket, _, key = uri.replace("s3://", "", 1).partition("/")
    return bucket, key


class S3Storage(StorageBackend):
    """Même layout de clés que le code historique (patients/<ID>/…, reports/<ID>/…)."""

    name = "s3"

    def __init__(self) -> None:
        # Import paresseux : boto3 n'est chargé qu'en mode aws.
        from config.aws_config import aws_config

        self._cfg = aws_config

    def _manager(self, bucket: str):
        from src.aws.s3_manager import S3Manager

        return S3Manager(bucket_name=bucket)

    def validate_input_uri(self, uri: str) -> str:
        uri = (uri or "").strip()
        if not _S3_URI_RE.match(uri):
            raise ValueError(f"URI S3 invalide: {uri}")
        return uri

    def is_managed(self, uri: str) -> bool:
        return bool(uri) and uri.startswith("s3://")

    def exists(self, uri: str) -> bool:
        if not self.is_managed(uri):
            return Path(uri).is_file()
        bucket, key = split_s3_uri(uri)
        return self._manager(bucket).file_exists(key, bucket_name=bucket)

    def fetch(self, uri: str, dest: Path) -> str:
        if not self.is_managed(uri):
            p = Path(uri)
            if p.is_file():
                return str(p.resolve())
            raise StorageError(f"Fichier introuvable: {uri}")
        from src.aws.s3_manager import S3ManagerError

        bucket, key = split_s3_uri(uri)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        s3 = self._manager(bucket)
        try:
            if not s3.file_exists(key, bucket_name=bucket):
                raise StorageError(f"Fichier S3 introuvable: {uri}")
            s3.download_file(key, str(dest), bucket_name=bucket, show_progress=False)
        except S3ManagerError as e:
            raise StorageError(str(e)) from e
        return str(dest)

    def put(self, local_path: str, key: str, area: str = "output", move: bool = False) -> str:
        if key.startswith("s3://"):
            bucket, key = split_s3_uri(key)
        else:
            bucket = (
                self._cfg.s3_input_bucket if area == "input" else self._cfg.s3_output_bucket
            )
        return self._manager(bucket).upload_file(
            local_path, key, bucket_name=bucket, show_progress=False
        )

    def key_for(self, patient_id: str, area: str, filename: str) -> str:
        if area == "input":
            return f"patients/{patient_id}/input/{filename}"
        if area == "report":
            return f"reports/{patient_id}/{filename}"
        return f"patients/{patient_id}/{filename}"
