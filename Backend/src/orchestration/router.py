"""Choosing the next tool among those that are ready.

The planner guarantees that every proposed tool is valid and independent of the others: the
chosen order therefore never changes the clinical result. The Mistral router (optional,
ORCHESTRATOR_DETERMINISTIC=false) is only consulted when there is a real choice to make.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Protocol, Sequence

from loguru import logger

from src.orchestration.registry import ToolSpec


class Router(Protocol):
    name: str

    def choose(self, candidates: Sequence[ToolSpec], ctx: Dict[str, Any], done: List[str]) -> ToolSpec: ...


class DeterministicRouter:
    name = "deterministic"

    def choose(self, candidates: Sequence[ToolSpec], ctx: Dict[str, Any], done: List[str]) -> ToolSpec:
        return candidates[0]


class LLMRouter:
    """Mistral (Ollama) picks among the candidates; any invalid answer → registry order."""

    name = "mistral"

    SYSTEM = (
        "You are the orchestrator of the GermlineIQ genomic pipeline. You are given tools that are ready "
        'to run. Reply only with JSON: {"next_tool": "<name>", "reason": "<short reason>"}.'
    )

    def __init__(self) -> None:
        from src.llm.ollama_client import OllamaClient

        self.client = OllamaClient()

    def choose(self, candidates: Sequence[ToolSpec], ctx: Dict[str, Any], done: List[str]) -> ToolSpec:
        if len(candidates) == 1:
            return candidates[0]
        names = [t.name for t in candidates]
        prompt = (
            f"Completed steps: {done}\n"
            "Ready tools:\n"
            + "\n".join(f"- {t.name} : {t.description}" for t in candidates)
            + "\nWhich one should run now?"
        )
        raw = self.client.generate(prompt, system=self.SYSTEM, temperature=0.0, seed=0)
        m = re.search(r"\{[^{}]*\}", raw or "", re.DOTALL)
        try:
            choice = json.loads(m.group())["next_tool"] if m else None
        except (json.JSONDecodeError, KeyError):
            choice = None
        if choice in names:
            logger.info(f"[Router mistral] {choice} parmi {names}")
            return candidates[names.index(choice)]
        logger.warning(f"[Router mistral] invalid answer ({raw!r:.80}) — registry order")
        return candidates[0]


def build_router() -> Router:
    deterministic = os.getenv("ORCHESTRATOR_DETERMINISTIC", "true").lower() in ("1", "true", "yes")
    return DeterministicRouter() if deterministic else LLMRouter()
