"""Cache des résultats d'outils coûteux (appel de variants : minutes sur GPU, heures sur CPU).

Clé = empreinte (outil + version + fichiers d'entrée [taille, mtime] + configuration + panel).
Une entrée n'est valide que si les fichiers qu'elle référence existent toujours.
Désactivable avec ZAYNB_CACHE=false.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from src.orchestration.registry import ToolSpec


def _fingerprint(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("/"):
        p = Path(value)
        if p.is_file():
            st = p.stat()
            return {"path": value, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
    return value


def _paths_exist(produced: Dict[str, Any]) -> bool:
    for v in produced.values():
        if isinstance(v, str) and v.startswith("/") and not Path(v).exists():
            return False
    return True


class ResultCache:
    def __init__(self, root: Path):
        self.root = Path(root)

    @classmethod
    def from_env(cls) -> Optional["ResultCache"]:
        if os.getenv("ZAYNB_CACHE", "true").lower() in ("0", "false", "no"):
            return None
        from config.settings import paths

        return cls(paths().cache_dir)

    def key(self, tool: ToolSpec, ctx: Dict[str, Any]) -> Optional[str]:
        if not tool.cache_inputs:
            return None
        from src.genomics.panel import get_panel

        material = {
            "tool": tool.name,
            "version": tool.version,
            "inputs": {k: _fingerprint(ctx.get(k)) for k in tool.cache_inputs},
            "env": {k: os.getenv(k) for k in tool.config_env},
            "panel": get_panel().sha256,
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

    def get(self, key: Optional[str]) -> Optional[Dict[str, Any]]:
        if not key:
            return None
        p = self.root / f"{key}.json"
        if not p.is_file():
            return None
        try:
            produced = json.loads(p.read_text())
        except json.JSONDecodeError:
            return None
        if not _paths_exist(produced):
            logger.info(f"Cache {key[:12]} périmé (fichiers supprimés)")
            return None
        return produced

    def put(self, key: Optional[str], produced: Dict[str, Any]) -> None:
        if not key:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.root / f"{key}.tmp"
        tmp.write_text(json.dumps(produced, sort_keys=True, default=str))
        tmp.replace(self.root / f"{key}.json")
