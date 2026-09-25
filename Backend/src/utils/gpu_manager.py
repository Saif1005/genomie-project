"""Gestionnaire VRAM GPU avec mutex d'exclusion (VRAM réelle détectée via nvidia-smi)."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Generator, List, Optional
from urllib.request import Request, urlopen

from loguru import logger

try:
    import torch

    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    torch = None  # type: ignore


class GPUPhase(str, Enum):
    IDLE = "idle"
    OLLAMA = "ollama"
    PARABRICKS = "parabricks"
    BIOGPT = "biogpt"


class GPUMutexError(RuntimeError):
    """Le GPU est déjà verrouillé par une autre phase."""


class GPUManager:
    """
    Orchestration VRAM déterministe avec mutex :
    Ollama suspendu → Parabricks (--gpus all) → destroy container → empty_cache → BioGPT.
    """

    def __init__(self) -> None:
        self.ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        self.orchestrator_model = os.getenv("ORCHESTRATOR_LLM_MODEL", "mistral:v0.3")
        self._lock = threading.RLock()
        self._phase: GPUPhase = GPUPhase.IDLE
        self._holder: Optional[str] = None

    @staticmethod
    def _ts() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _acquire(self, phase: GPUPhase, holder: str) -> None:
        with self._lock:
            if self._phase != GPUPhase.IDLE and self._phase != phase:
                raise GPUMutexError(
                    f"GPU occupé par {self._phase.value} (holder={self._holder}), "
                    f"impossible d'acquérir {phase.value}"
                )
            self._phase = phase
            self._holder = holder
            self.log_transition_json("gpu_mutex", "ACQUIRED", holder=holder)

    def _release(self, phase: GPUPhase, holder: str) -> None:
        with self._lock:
            if self._phase == phase and self._holder == holder:
                self._phase = GPUPhase.IDLE
                self._holder = None
                self.log_transition_json("gpu_mutex", "RELEASED", holder=holder)

    @contextmanager
    def gpu_phase(self, phase: GPUPhase, agent: str) -> Generator[None, None, None]:
        """Verrou d'exclusion mutuelle pour une phase GPU."""
        if phase == GPUPhase.PARABRICKS:
            self.prepare_for_parabricks()
        elif phase == GPUPhase.BIOGPT:
            self.prepare_for_biogpt()
        self._acquire(phase, agent)
        try:
            yield
        finally:
            if phase == GPUPhase.PARABRICKS:
                self.release_after_parabricks()
            self._release(phase, agent)

    def get_vram_stats(self) -> Dict[str, Any]:
        stats: Dict[str, Any] = {
            "timestamp": self._ts(),
            "gpu_phase": self._phase.value,
            "gpu_holder": self._holder,
            "gpu_available": False,
            "allocated_mb": 0.0,
            "reserved_mb": 0.0,
            "total_mb": 0.0,
            "free_mb": 0.0,
            "utilization_pct": None,
        }
        if HAS_TORCH and torch.cuda.is_available():
            stats["gpu_available"] = True
            idx = torch.cuda.current_device()
            stats["device"] = torch.cuda.get_device_name(idx)
            stats["allocated_mb"] = round(torch.cuda.memory_allocated(idx) / 1024**2, 1)
            stats["reserved_mb"] = round(torch.cuda.memory_reserved(idx) / 1024**2, 1)
            props = torch.cuda.get_device_properties(idx)
            stats["total_mb"] = round(props.total_memory / 1024**2, 1)
            stats["free_mb"] = round(stats["total_mb"] - stats["allocated_mb"], 1)
        try:
            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used,memory.total,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=10,
            )
            parts = [p.strip() for p in out.strip().split(",")]
            if len(parts) >= 2:
                stats["nvidia_used_mb"] = float(parts[0])
                stats["nvidia_total_mb"] = float(parts[1])
                if len(parts) >= 3:
                    stats["utilization_pct"] = float(parts[2])
        except (subprocess.SubprocessError, FileNotFoundError, ValueError):
            pass
        return stats

    def log_transition_json(
        self,
        agent: str,
        phase: str,
        duration_s: Optional[float] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Log structuré JSON à chaque transition d'agent."""
        vram = self.get_vram_stats()
        entry: Dict[str, Any] = {
            "event": "agent_transition",
            "timestamp": self._ts(),
            "agent": agent,
            "phase": phase,
            "duration_s": round(duration_s, 3) if duration_s is not None else None,
            "gpu_phase": self._phase.value,
            "vram": vram,
            "memory_mb": {
                "allocated": vram.get("allocated_mb"),
                "nvidia_used": vram.get("nvidia_used_mb"),
                "total": vram.get("total_mb"),
            },
        }
        if extra:
            entry.update(extra)
        logger.info(json.dumps(entry, default=str))
        return entry

    def log_transition(
        self,
        agent: str,
        phase: str,
        duration_s: Optional[float] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.log_transition_json(agent, phase, duration_s=duration_s, **(extra or {}))

    def empty_cuda_cache(self) -> None:
        if HAS_TORCH and torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
        self.log_transition_json("gpu_manager", "CUDA_CACHE_CLEARED")

    def _ollama_request(self, path: str, payload: Optional[Dict] = None) -> Dict[str, Any]:
        url = f"{self.ollama_host}{path}"
        data = json.dumps(payload or {}).encode("utf-8")
        req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError) as e:  # URLError, timeout, JSON invalide
            logger.warning(f"Ollama indisponible ({url}): {e}")
            return {}

    def _ollama_running_models(self) -> List[str]:
        try:
            req = Request(f"{self.ollama_host}/api/ps", method="GET")
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
        except (OSError, ValueError):
            return []

    def suspend_ollama_models(self) -> None:
        """a) Suspension Ollama/Mistral — libération totale VRAM."""
        with self._lock:
            models = self._ollama_running_models() or [self.orchestrator_model]
            for model in models:
                self._ollama_request(
                    "/api/generate",
                    {"model": model, "prompt": "", "keep_alive": 0},
                )
                try:
                    subprocess.run(
                        ["ollama", "stop", model],
                        capture_output=True,
                        timeout=30,
                        check=False,
                    )
                except (subprocess.SubprocessError, FileNotFoundError):
                    pass
            self.empty_cuda_cache()
            self.log_transition_json("ollama", "SUSPENDED", models=models)

    def prepare_for_parabricks(self) -> None:
        """Séquence pré-Parabricks : suspend Ollama + vide cache CUDA."""
        self.log_transition_json("gpu_manager", "PRE_PARABRICKS_START")
        self.suspend_ollama_models()
        self.empty_cuda_cache()
        self.log_transition_json("gpu_manager", "PRE_PARABRICKS_READY")

    def _vram_allows_sharing(self) -> bool:
        """True si la VRAM réelle permet à Ollama (Mistral) et BioGPT de cohabiter."""
        shared_gb = float(os.getenv("GPU_SHARED_VRAM_GB", "24"))
        max_mb = max((g["vram_mb"] for g in gpu_inventory()), default=0.0)
        return max_mb / 1024 >= shared_gb

    def prepare_for_biogpt(self) -> None:
        """e) Prépare VRAM pour BioGPT après Parabricks."""
        if self._vram_allows_sharing():
            self.log_transition_json("gpu_manager", "OLLAMA_KEPT_VRAM_SUFFICIENT")
        else:
            self.suspend_ollama_models()
        self.empty_cuda_cache()
        self.log_transition_json("gpu_manager", "PRE_BIOGPT_READY")

    def release_after_parabricks(self) -> None:
        """c-d) Post-Parabricks : destroy containers + empty_cache."""
        destroy_cmd = (
            "docker ps -aq --filter name=parabricks- 2>/dev/null | "
            "xargs -r docker rm -f 2>/dev/null || true"
        )
        try:
            subprocess.run(destroy_cmd, shell=True, capture_output=True, timeout=60)
        except subprocess.SubprocessError:
            pass
        self.empty_cuda_cache()
        self.log_transition_json("parabricks", "CONTAINER_DESTROYED_VRAM_RELEASED")

    def after_agent_step(self, agent: str, duration_s: float) -> None:
        self.empty_cuda_cache()
        self.log_transition_json(agent, "STEP_COMPLETE", duration_s=duration_s)

    @contextmanager
    def parabricks_vram(self) -> Generator[None, None, None]:
        with self.gpu_phase(GPUPhase.PARABRICKS, agent="parabricks"):
            yield

    def unload_hf_model(self, model: Any) -> None:
        if model is None:
            return
        try:
            del model
        except Exception:
            pass
        self.empty_cuda_cache()
        self.log_transition_json("huggingface", "MODEL_UNLOADED")


_gpu_inventory_cache: Optional[List[Dict[str, Any]]] = None


def gpu_inventory(refresh: bool = False) -> List[Dict[str, Any]]:
    """GPUs NVIDIA réellement visibles (nvidia-smi) — liste vide si aucun."""
    global _gpu_inventory_cache
    if _gpu_inventory_cache is not None and not refresh:
        return _gpu_inventory_cache
    gpus: List[Dict[str, Any]] = []
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                gpus.append(
                    {
                        "name": parts[0],
                        "vram_mb": float(parts[1]),
                        "compute_cap": parts[2] if len(parts) > 2 else None,
                    }
                )
    except (subprocess.SubprocessError, FileNotFoundError, ValueError):
        pass
    _gpu_inventory_cache = gpus
    return gpus


def select_pipeline_backend() -> Dict[str, Any]:
    """
    Choisit le moteur FASTQ→VCF selon la VRAM réelle du serveur.

    PIPELINE_BACKEND=auto (défaut) | parabricks | cpu
    PARABRICKS_MIN_VRAM_GB : VRAM minimale par GPU exigée par Parabricks (16 Go).
    """
    from config.settings import parabricks

    requested = os.getenv("PIPELINE_BACKEND", "auto").lower()
    min_gb = parabricks().min_vram_gb
    gpus = gpu_inventory()
    max_vram_gb = max((g["vram_mb"] for g in gpus), default=0.0) / 1024
    info: Dict[str, Any] = {
        "requested": requested,
        "gpus": gpus,
        "max_vram_gb": round(max_vram_gb, 1),
        "parabricks_min_vram_gb": min_gb,
    }
    if requested == "cpu":
        info.update(backend="cpu", reason="PIPELINE_BACKEND=cpu")
    elif requested == "parabricks":
        info.update(backend="parabricks", reason="PIPELINE_BACKEND=parabricks")
    elif not gpus:
        info.update(backend="cpu", reason="Aucun GPU NVIDIA détecté")
    elif max_vram_gb + 1.0 < min_gb:  # tolérance : une carte « 16 Go » expose 15,0-15,9 Go (ex. 15 360 MiB)
        info.update(
            backend="cpu",
            reason=f"VRAM {max_vram_gb:.1f} Go < {min_gb:.0f} Go requis par Parabricks",
        )
    else:
        info.update(backend="parabricks", reason=f"GPU {gpus[0]['name']} ({max_vram_gb:.1f} Go)")
    return info


class CUDACompatibilityError(RuntimeError):
    """CUDA requis mais indisponible ou incompatible."""


def assert_cuda_operational() -> Dict[str, Any]:
    if os.getenv("ALLOW_CPU_FALLBACK", "").lower() in ("1", "true", "yes"):
        logger.warning("ALLOW_CPU_FALLBACK actif — skip vérification CUDA")
        return {}
    if not HAS_TORCH:
        raise CUDACompatibilityError(
            "PyTorch non installé. pip install torch --index-url "
            "https://download.pytorch.org/whl/cu118"
        )
    if not torch.cuda.is_available():
        raise CUDACompatibilityError("CUDA non disponible")
    try:
        probe = torch.zeros(1, device="cuda")
        del probe
        torch.cuda.synchronize()
        _ = torch.cuda.get_device_name(0)
    except Exception as e:
        raise CUDACompatibilityError(f"CUDA non opérationnel: {e}") from e
    stats = GPUManager().get_vram_stats()
    logger.info(json.dumps({"event": "cuda_check", "status": "ok", "vram": stats}))
    return stats


_gpu_manager: Optional[GPUManager] = None


def get_gpu_manager() -> GPUManager:
    global _gpu_manager
    if _gpu_manager is None:
        _gpu_manager = GPUManager()
    return _gpu_manager
