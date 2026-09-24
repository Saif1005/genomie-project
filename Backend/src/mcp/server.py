"""Serveur MCP stdio (JSON-RPC ligne par ligne) : expose le registre d'outils ZAYNB.

- tools/list : description générée depuis src.orchestration.registry (même source que le moteur)
- tools/call : exécute un outil sur le contexte de session (les sorties s'accumulent)
- zaynb/run  : exécute tout le plan (orchestrateur) pour les entrées fournies

Lancement : python -m src.mcp.server
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional

from src import __version__
from src.core import context as K
from src.orchestration.engine import Orchestrator
from src.orchestration.registry import TOOLS_BY_NAME, list_tools


class ZaynbMCPServer:
    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, orchestrator: Optional[Orchestrator] = None):
        self.orchestrator = orchestrator or Orchestrator()
        self.session: Dict[str, Any] = {}
        self._handlers = {
            "initialize": self._initialize,
            "tools/list": lambda _p: {"tools": list_tools()},
            "tools/call": self._call,
            "zaynb/run": self._run,
        }

    def _initialize(self, _params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "zaynb-pipeline", "version": __version__},
        }

    @staticmethod
    def _text(payload: Dict[str, Any], is_error: bool = False) -> Dict[str, Any]:
        return {"content": [{"type": "text", "text": json.dumps(payload, default=str)}], "isError": is_error}

    def _call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name = params.get("name", "")
        if name not in TOOLS_BY_NAME:
            return self._text({"error": f"outil inconnu : {name}"}, is_error=True)
        self.session.update(params.get("arguments") or {})
        tool = TOOLS_BY_NAME[name]
        missing = [k for k in tool.requires if not self.session.get(k)]
        if missing:
            return self._text({"error": f"entrées manquantes : {missing}"}, is_error=True)
        record, produced = self.orchestrator.run_tool(name, self.session)
        self.session.update(produced)
        return self._text({"step": record.to_dict(), "produced": sorted(produced)}, is_error=record.status == "failed")

    def _run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        result = self.orchestrator.run(params.get("arguments") or {})
        self.session = result.context
        return self._text({
            "success": result.success,
            "plan": result.plan,
            "steps": [s.to_dict() for s in result.steps],
            "error": result.error,
            "risk_level": (result.context.get(K.PREDICTION_RESULTS) or {}).get("risk_level"),
            "report": result.context.get(K.REPORT_URI),
        }, is_error=not result.success)

    def handle(self, request: Dict[str, Any]) -> Dict[str, Any]:
        req_id = request.get("id")
        handler = self._handlers.get(request.get("method", ""))
        if handler is None:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Méthode inconnue : {request.get('method')}"}}
        try:
            return {"jsonrpc": "2.0", "id": req_id, "result": handler(request.get("params") or {})}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e)}}

    def serve_stdio(self) -> None:
        for line in sys.stdin:
            if line.strip():
                sys.stdout.write(json.dumps(self.handle(json.loads(line)), default=str) + "\n")
                sys.stdout.flush()


def main() -> None:
    ZaynbMCPServer().serve_stdio()


if __name__ == "__main__":
    main()
