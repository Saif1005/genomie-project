"""DataManagerAgent — valide la paire FASTQ et la range dans le stockage patient."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from src.core import context as K
from src.core.agent import AgentError, AgentResult, BaseAgent
from src.storage import get_storage
from src.utils.validators import ValidationError, validate_fastq_files


class DataManagerAgent(BaseAgent):
    def __init__(self, config: Dict[str, Any] | None = None):
        super().__init__("DataManager", config)
        self.storage = get_storage()

    def _store(self, patient_id: str, uri: str) -> str:
        if self.storage.is_managed(uri):
            return uri
        key = self.storage.key_for(patient_id, "input", Path(uri).name)
        return self.storage.put(uri, key, area="input")

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        pid = context[K.PATIENT_ID]
        r1, r2 = context[K.FASTQ_R1], context[K.FASTQ_R2]
        try:
            validate_fastq_files(r1, r2)
        except ValidationError as e:
            raise AgentError(f"FASTQ invalides : {e}") from e
        return AgentResult.ok(**{K.FASTQ_R1_URI: self._store(pid, r1), K.FASTQ_R2_URI: self._store(pid, r2)})
