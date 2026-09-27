"""LLMTrainingAgent — appends the patient example to the LoRA training set (optional tool, train_llm=true).

Fine-tuning itself runs offline on the server (python -m src.llm.finetune).
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
        with open(path, "a", encoding="utf-8") as fh:  # O(1) append, no file rewrite
            fh.write(json.dumps(example, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        self.logger.info(f"Training example appended: {path}")
        return AgentResult.ok(**{K.TRAINING_DATA: str(path)})
