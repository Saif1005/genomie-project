"""Inference engine for fine-tuned LLM cancer detection."""

import json
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger

try:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
    from peft import PeftModel
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    torch = None
    AutoModelForCausalLM = None
    AutoTokenizer = None
    PeftModel = None

from config.llm_config import llm_config
from src.llm.prompt_templates import GenomicPromptTemplates


class InferenceError(Exception):
    """Custom exception for inference errors."""

    pass


class CancerDetectionInference:
    """Inference engine for cancer detection using fine-tuned LLM."""

    def __init__(
        self,
        model_path: Optional[str] = None,
        base_model: Optional[str] = None,
        device: Optional[str] = None,
    ):
        """
        Initialize inference engine.

        Args:
            model_path: Path to fine-tuned model (LoRA adapters)
            base_model: Base model name (defaults to config)
            device: Device to use (defaults to config)
        """
        self.model_path = model_path or llm_config.model_path
        self.base_model = base_model or llm_config.model_name
        self.device = device or llm_config.device
        # Fallback CPU si CUDA indisponible (serveur sans GPU compatible)
        if HAS_TORCH and self.device in ("cuda", "auto"):
            if torch.cuda.is_available():
                self.device = "cuda"
            else:
                logger.warning("CUDA indisponible — BioGPT exécuté sur CPU (plus lent)")
                self.device = "cpu"

        self.model = None
        self.tokenizer = None
        self.prompt_templates = GenomicPromptTemplates()

    def unload_model(self) -> None:
        """Libère BioGPT / HF de la VRAM."""
        from src.utils.gpu_manager import get_gpu_manager

        gpu = get_gpu_manager()
        if self.model is not None:
            gpu.unload_hf_model(self.model)
            self.model = None
        self.tokenizer = None
        gpu.empty_cuda_cache()

    def load_model(self) -> None:
        """Load fine-tuned model and tokenizer."""
        if not HAS_TORCH:
            raise InferenceError("PyTorch and transformers are required for inference. Install with: pip install torch transformers peft")
        
        if self.model is not None:
            return

        logger.info(f"Loading model from {self.model_path}")
        logger.info(f"Base model: {self.base_model}")
        model_path = Path(self.model_path)

        try:
            self.tokenizer = AutoTokenizer.from_pretrained(self.base_model)
            if self.tokenizer.pad_token is None:
                self.tokenizer.pad_token = self.tokenizer.eos_token
            self._load_weights(model_path)
            self.model.eval()
            logger.info(f"Model loaded successfully on device={self.device}")

        except Exception as e:
            error_msg = f"Failed to load model: {e}"
            logger.error(error_msg)
            raise InferenceError(error_msg) from e

    def _load_weights(self, model_path: Path) -> None:
        dtype = torch.float16 if self.device == "cuda" and torch.cuda.is_available() else torch.float32
        self.model = AutoModelForCausalLM.from_pretrained(
            self.base_model,
            device_map="auto" if self.device == "cuda" else None,
            torch_dtype=dtype,
        )
        if self.device == "cuda" and not hasattr(self.model, "hf_device_map"):
            self.model = self.model.to(self.device)
        elif self.device == "cpu":
            self.model = self.model.to("cpu")
        if model_path.exists():
            logger.info(f"Loading LoRA adapters from {model_path}")
            self.model = PeftModel.from_pretrained(self.model, str(model_path))
            self.model = self.model.merge_and_unload()

    def analyze_patient(
        self,
        patient_id: str,
        variants: List[Dict],
        coverage: float,
        tmb: Optional[float] = None,
        quality_metrics: Optional[Dict] = None,
    ) -> Dict:
        """
        Analyze patient genomic data for cancer detection.

        Args:
            patient_id: Patient identifier
            variants: List of pathogenic variants
            coverage: Sequencing coverage
            tmb: Tumor mutational burden (optional)
            quality_metrics: Quality metrics (optional)

        Returns:
            Dictionary with analysis results
        """
        if self.model is None:
            self.load_model()

        # Format prompt
        user_prompt = self.prompt_templates.format_user_prompt(
            patient_id=patient_id,
            variants=variants,
            coverage=coverage,
            tmb=tmb,
            quality_metrics=quality_metrics,
        )

        # Create messages
        messages = [
            {"role": "system", "content": GenomicPromptTemplates.SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        # Tokenize (BioGPT n'a pas de chat template)
        if (
            hasattr(self.tokenizer, "apply_chat_template")
            and getattr(self.tokenizer, "chat_template", None)
        ):
            text = self.tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
        else:
            text = (
                f"{GenomicPromptTemplates.SYSTEM_PROMPT}\n\n"
                f"{user_prompt}\n\nAnalysis:"
            )
        inputs = self.tokenizer(text, return_tensors="pt")
        device = next(self.model.parameters()).device
        inputs = {k: v.to(device) for k, v in inputs.items()}

        # Generate
        logger.info("Generating response from LLM...")
        with torch.no_grad():
            outputs = self.model.generate(
                **inputs,
                max_new_tokens=512,
                temperature=llm_config.temperature,
                top_p=llm_config.top_p,
                do_sample=True,
                pad_token_id=self.tokenizer.eos_token_id,
            )

        # Decode only newly generated tokens (BioGPT n'utilise pas de chat template)
        input_len = inputs["input_ids"].shape[1]
        generated_ids = outputs[0][input_len:]
        assistant_response = self.tokenizer.decode(
            generated_ids, skip_special_tokens=True
        ).strip()

        results = self._parse_response(assistant_response)
        results["prediction_mode"] = "biogpt"
        results["model_name"] = self.base_model

        logger.info(f"Analysis completed for patient {patient_id}")
        return results

    def _parse_response(self, response: str) -> Dict:
        """
        Parse LLM response into structured format.

        Args:
            response: LLM response text

        Returns:
            Structured results dictionary
        """
        results = {
            "cancer_detected": False,
            "cancer_types": [],
            "risk_level": "LOW",
            "risk_score": 0.0,
            "diagnostic_conclusion": "Risque génétique de cancer du sein : FAIBLE",
            "clinical_summary": "",
            "legal_disclaimer": (
                "Ce rapport est généré par un système d'intelligence artificielle à des fins "
                "d'aide à la décision. Il ne constitue pas un diagnostic médical et doit être "
                "validé par un oncologue ou un généticien clinique avant toute prise de décision thérapeutique."
            ),
            "status": "AWAITING_MEDICAL_VALIDATION",
            "recommendations": [],
            "raw_response": response,
        }

        lines = response.split("\n")
        for line in lines:
            line = line.strip()
            if line.upper().startswith("RISK_LEVEL:"):
                results["risk_level"] = line.split(":", 1)[-1].strip().upper()
            elif line.upper().startswith("DIAGNOSTIC_CONCLUSION:"):
                results["diagnostic_conclusion"] = line.split(":", 1)[-1].strip()
            elif line.upper().startswith("CLINICAL_SUMMARY:"):
                results["clinical_summary"] = line.split(":", 1)[-1].strip()
            elif line.startswith("RÉSULTAT:"):
                result = line.split(":")[-1].strip()
                results["cancer_detected"] = result.upper() == "POSITIF"
            elif line.startswith("Niveau de risque"):
                level = line.split(":")[-1].strip().upper()
                results["risk_level"] = level

        results["cancer_detected"] = results["risk_level"] in ("HIGH", "MODERATE")
        if results["cancer_detected"]:
            results["cancer_types"] = ["breast"]
        risk_scores = {"HIGH": 85.0, "MODERATE": 55.0, "LOW": 15.0}
        results["risk_score"] = risk_scores.get(results["risk_level"], 15.0)
        return results

    def batch_analyze(
        self, patients_data: List[Dict]
    ) -> List[Dict]:
        """
        Analyze multiple patients in batch.

        Args:
            patients_data: List of patient data dictionaries

        Returns:
            List of analysis results
        """
        results = []
        for patient_data in patients_data:
            try:
                result = self.analyze_patient(**patient_data)
                result["patient_id"] = patient_data.get("patient_id")
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to analyze patient {patient_data.get('patient_id')}: {e}")
                results.append({
                    "patient_id": patient_data.get("patient_id"),
                    "error": str(e),
                })

        return results



