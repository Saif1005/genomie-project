"""Exécution des commandes du pipeline sur le serveur (bash -c, pipefail)."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional, Protocol

from loguru import logger


class PipelineError(RuntimeError):
    """Échec d'une étape du pipeline (message avec la fin de stderr)."""


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class Executor(Protocol):
    def run(self, command: str, timeout: Optional[int] = None) -> CommandResult: ...


class LocalExecutor:
    def run(self, command: str, timeout: Optional[int] = None) -> CommandResult:
        logger.debug(f"$ {command[:300]}")
        try:
            p = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise PipelineError(f"Délai dépassé ({timeout}s) : {command[:120]}…") from e
        return CommandResult(p.returncode, p.stdout, p.stderr)
