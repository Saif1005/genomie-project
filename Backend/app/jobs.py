"""Jobs d'analyse : file d'exécution + état persisté sur disque (survit à un redémarrage)."""

from __future__ import annotations

import json
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from loguru import logger

from app.schemas import JobStatus
from config.settings import paths
from src.core import context as K
from src.orchestration.engine import Orchestrator
from src.orchestration.registry import TOOLS
from src.storage import get_storage

_LABELS = {t.ui_step: t.label for t in TOOLS}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """Un fichier JSON par job sous tmp/jobs (écriture atomique)."""

    def __init__(self, directory: Path):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = Lock()
        self._load()

    def _load(self) -> None:
        for f in sorted(self.dir.glob("*.json")):
            try:
                job = json.loads(f.read_text())
            except json.JSONDecodeError:
                continue
            if job.get("status") in (JobStatus.QUEUED.value, JobStatus.RUNNING.value):
                job.update(status=JobStatus.FAILED.value, error="Interrompu par un redémarrage du serveur",
                           progress_message="Interrompu — relancez l'analyse (les étapes faites seront reprises)")
                self._write(job)
            self._jobs[job["job_id"]] = job

    def _write(self, job: Dict[str, Any]) -> None:
        tmp = self.dir / f"{job['job_id']}.tmp"
        tmp.write_text(json.dumps(job, ensure_ascii=False, default=str))
        tmp.replace(self.dir / f"{job['job_id']}.json")

    def create(self, patient_id: str, mode: str, plan: List[str], vcf_path: Optional[str] = None) -> str:
        job_id = str(uuid.uuid4())
        job = {
            "job_id": job_id, "patient_id": patient_id, "mode": mode, "vcf_path": vcf_path, "plan": plan,
            "status": JobStatus.QUEUED.value, "current_step": None, "progress_message": "En file d'attente",
            "created_at": _now(), "updated_at": _now(), "steps_completed": [], "error": None, "result": None,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._write(job)
        return job_id

    def update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.update({k: (v.value if isinstance(v, JobStatus) else v) for k, v in fields.items()})
            job["updated_at"] = _now()
            self._write(job)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None


class JobService:
    """Exécute les analyses une par une (GPU/CPU partagés) sans bloquer l'API."""

    def __init__(self, store: JobStore):
        self.store = store
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pipeline")

    def _on_step(self, job_id: str):
        def callback(step: str, phase: str, duration: Optional[float] = None) -> None:
            label = _LABELS.get(step, step)
            if phase == "running":
                self.store.update(job_id, current_step=step, progress_message=f"En cours : {label}")
            elif phase == "completed":
                done = list((self.store.get(job_id) or {}).get("steps_completed", []))
                if step not in done:
                    done.append(step)
                suffix = f" ({duration:.0f}s)" if duration else ""
                self.store.update(job_id, steps_completed=done, progress_message=f"Terminé : {label}{suffix}")
            elif phase == "failed":
                self.store.update(job_id, progress_message=f"Échec : {label}")

        return callback

    def _run(self, job_id: str, context: Dict[str, Any]) -> None:
        self.store.update(job_id, status=JobStatus.RUNNING, progress_message="Démarrage de l'orchestrateur")
        try:
            result = Orchestrator(on_step=self._on_step(job_id)).run(context)
        except Exception as e:  # jamais de job bloqué en « running »
            logger.exception(f"job={job_id}")
            self.store.update(job_id, status=JobStatus.FAILED, error=str(e), current_step=None, progress_message="Erreur")
            return
        self.store.update(
            job_id,
            status=JobStatus.COMPLETED if result.success else JobStatus.FAILED,
            steps_completed=result.steps_completed,
            current_step=None,
            progress_message=f"Terminé en {result.duration:.0f}s" if result.success else "Échec",
            error=result.error,
            result=result.context.get(K.CLINICAL_REPORT),
        )

    def submit(self, job_id: str, context: Dict[str, Any]) -> None:
        self._executor.submit(self._run, job_id, context)

    def submit_upload(self, job_id: str, patient_id: str, files: List[Path], train_llm: bool) -> None:
        """FASTQ reçus par upload : déplacés dans patients/<ID>/input puis analysés."""

        def task() -> None:
            storage = get_storage()
            try:
                stored = [
                    storage.put(str(f), storage.key_for(patient_id, "input", f.name), area="input", move=True)
                    for f in files
                ]
            except Exception as e:
                self.store.update(job_id, status=JobStatus.FAILED, error=str(e), progress_message="Échec de l'enregistrement")
                return
            finally:
                if files:
                    shutil.rmtree(files[0].parent, ignore_errors=True)
            self._run(job_id, {K.PATIENT_ID: patient_id, K.FASTQ_R1: stored[0], K.FASTQ_R2: stored[1], K.TRAIN_LLM: train_llm})

        self._executor.submit(task)


_services: Dict[str, JobService] = {}


def get_job_service() -> JobService:
    root = str(paths().data_root)
    if root not in _services:
        _services[root] = JobService(JobStore(paths().data_root / "tmp" / "jobs"))
    return _services[root]
