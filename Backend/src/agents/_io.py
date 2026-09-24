"""Entrées/sorties communes aux agents : répertoire patient et publication d'artefacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict

from src.storage import get_storage


def patient_output_dir(patient_id: str) -> Path:
    """patients/<ID>/output (créé si besoin)."""
    return get_storage().local_patient_dir(patient_id, "output")


def write_artifact(patient_id: str, filename: str, payload: Dict[str, Any]) -> str:
    """Écrit un JSON trié (diff-able, reproductible) et le publie dans le stockage patient."""
    path = patient_output_dir(patient_id) / filename
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, default=str))
    tmp.replace(path)
    storage = get_storage()
    return storage.put(str(path), storage.key_for(patient_id, "output", filename), area="output")


def read_artifact(uri: str, patient_id: str) -> Dict[str, Any]:
    local = get_storage().fetch(uri, patient_output_dir(patient_id) / Path(uri).name)
    return json.loads(Path(local).read_text())


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()
