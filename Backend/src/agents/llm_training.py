"""LLMTrainingAgent — ajoute l'exemple patient au jeu d'entraînement LoRA (outil optionnel, train_llm=true).

Le fine-tuning lui-même se lance hors ligne, sur le serveur, à partir de ce fichier JSONL.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from config.settings import paths
from src.core import context as K
from src.core.agent import AgentResult, BaseAgent


class LLMTrainingAgent(BaseAgent):
    def __init__(self, config: Dict[str, Any] | None = None):
        super().__init__("LLMTraining", config)

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        from src.llm.data_preparation import TrainingDataPreparation

        example = TrainingDataPreparation().prepare_from_metrics_json(
            context[K.VCF_METRICS], analysis_result=context[K.PREDICTION_RESULTS]
        )
        path = paths().training_data
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:  # ajout O(1), pas de réécriture du fichier
            fh.write(json.dumps(example, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        self.logger.info(f"Exemple d'entraînement ajouté : {path}")
        return AgentResult.ok(**{K.TRAINING_DATA: str(path)})
