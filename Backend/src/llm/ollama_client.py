"""Client Ollama (Mistral) — routeur d'orchestration optionnel et assistant conversationnel."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from loguru import logger


class OllamaClient:
    """Inférence locale via Ollama (mistral:v0.3, Q4_K_M ~4-bit)."""

    def __init__(
        self,
        model: Optional[str] = None,
        host: Optional[str] = None,
        keep_alive: str = "5m",
    ) -> None:
        self.model = model or os.getenv("ORCHESTRATOR_LLM_MODEL", "mistral:v0.3")
        self.host = (host or os.getenv("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")
        self.keep_alive = keep_alive

    def _post(self, path: str, payload: Dict[str, Any], timeout: int = 120) -> Dict[str, Any]:
        url = f"{self.host}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        temperature: Optional[float] = None,
        seed: Optional[int] = None,
    ) -> str:
        messages: List[Dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        options: Dict[str, Any] = {
            "temperature": (
                temperature
                if temperature is not None
                else float(os.getenv("ORCHESTRATOR_TEMPERATURE", "0.1"))
            ),
            "num_ctx": int(os.getenv("ORCHESTRATOR_NUM_CTX", "4096")),
        }
        if seed is not None:
            options["seed"] = seed
        try:
            data = self._post(
                "/api/chat",
                {
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "keep_alive": self.keep_alive,
                    "options": options,
                },
            )
            return data.get("message", {}).get("content", "")
        except URLError as e:
            logger.warning(f"Ollama chat failed: {e}")
            return ""
