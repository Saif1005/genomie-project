"""Choix de l'outil suivant parmi ceux qui sont prêts.

Le planificateur garantit que tous les outils proposés sont valides et indépendants entre eux :
l'ordre choisi ne change donc jamais le résultat clinique. Le routeur Mistral (optionnel,
ORCHESTRATOR_DETERMINISTIC=false) n'est consulté que s'il y a réellement un choix à faire.
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
    """Mistral (Ollama) choisit parmi les candidats ; toute réponse invalide → ordre du registre."""

    name = "mistral"

    SYSTEM = (
        "Tu es l'orchestrateur du pipeline génomique ZAYNB. On te donne des outils prêts à "
        'être exécutés. Réponds uniquement en JSON : {"next_tool": "<nom>", "reason": "<courte raison>"}.'
    )

    def __init__(self) -> None:
        from src.llm.ollama_client import OllamaClient

        self.client = OllamaClient()

    def choose(self, candidates: Sequence[ToolSpec], ctx: Dict[str, Any], done: List[str]) -> ToolSpec:
        if len(candidates) == 1:
            return candidates[0]
        names = [t.name for t in candidates]
        prompt = (
            f"Étapes terminées : {done}\n"
            "Outils prêts :\n"
            + "\n".join(f"- {t.name} : {t.description}" for t in candidates)
            + "\nLequel exécuter maintenant ?"
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
        logger.warning(f"[Router mistral] réponse invalide ({raw!r:.80}) — ordre du registre")
        return candidates[0]


def build_router() -> Router:
    deterministic = os.getenv("ORCHESTRATOR_DETERMINISTIC", "true").lower() in ("1", "true", "yes")
    return DeterministicRouter() if deterministic else LLMRouter()
