"""Moteur d'orchestration multi-agent (LangGraph).

Boucle « planifier → exécuter » :
  1. plan    : le planificateur recalcule, depuis le contexte courant, les outils encore
               nécessaires ; le routeur choisit parmi ceux qui sont prêts.
  2. execute : l'outil choisi tourne (ou plusieurs outils non exclusifs en parallèle), avec
               gestion VRAM, cache des résultats coûteux, et fusion déterministe du contexte.
Le graphe s'arrête quand les objectifs sont atteints ou à la première erreur.
Sans LangGraph installé, la même boucle tourne en Python pur (comportement identique).
"""

from __future__ import annotations

import copy
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, TypedDict

from loguru import logger

from src.core import context as K
from src.core.agent import AgentResult
from src.orchestration.cache import ResultCache
from src.orchestration.planner import Planner, PlanningError
from src.orchestration.registry import GPU_BIOGPT, GPU_PARABRICKS, TOOLS_BY_NAME, ToolSpec
from src.orchestration.router import Router, build_router

try:
    from langgraph.graph import END, StateGraph

    HAS_LANGGRAPH = True
except ImportError:  # pragma: no cover - dépend de l'installation
    HAS_LANGGRAPH = False

StepCallback = Callable[..., None]  # on_step(ui_step, phase, duration=None)
_UNSET = object()


@dataclass
class StepRecord:
    tool: str
    ui_step: str
    status: str  # completed | cached | failed
    duration: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RunResult:
    success: bool
    context: Dict[str, Any]
    steps: List[StepRecord] = field(default_factory=list)
    plan: List[str] = field(default_factory=list)
    error: Optional[str] = None
    duration: float = 0.0
    router: str = "deterministic"
    engine: str = "langgraph"

    @property
    def steps_completed(self) -> List[str]:
        return [s.ui_step for s in self.steps if s.status in ("completed", "cached")]


class _State(TypedDict, total=False):
    ctx: Dict[str, Any]
    done: List[str]
    steps: List[Dict[str, Any]]
    plan: List[str]
    next: List[str]
    error: Optional[str]
    waived: List[str]  # objectifs abandonnés après l'échec d'un outil non critique


class _NoGPU:
    """Gestionnaire GPU neutre (tests, mode VCF sans GPU)."""

    def __getattr__(self, _name: str) -> Callable[..., None]:
        return lambda *a, **k: None


class Orchestrator:
    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        planner: Optional[Planner] = None,
        router: Optional[Router] = None,
        cache: Any = _UNSET,
        on_step: Optional[StepCallback] = None,
        gpu: Any = None,
        max_parallel: int = 4,
    ):
        self.config = config or {}
        self.planner = planner or Planner()
        self.router = router or build_router()
        self.cache: Optional[ResultCache] = ResultCache.from_env() if cache is _UNSET else cache
        self.on_step = on_step or self.config.get("on_step")
        self.max_parallel = max_parallel
        if gpu is None:
            from src.utils.gpu_manager import get_gpu_manager

            gpu = get_gpu_manager()
        self.gpu = gpu

    # --- API publique ---------------------------------------------------------
    def run(self, initial: Dict[str, Any]) -> RunResult:
        start = time.perf_counter()
        ctx = K.validate_initial(dict(initial))
        state: _State = {"ctx": ctx, "done": [], "steps": [], "plan": [], "next": [], "error": None, "waived": []}
        try:
            state["plan"] = [t.name for t in self.planner.plan(ctx)]
        except PlanningError as e:
            return RunResult(False, ctx, error=str(e), router=self.router.name)
        logger.info(f"[Orchestrator] plan initial : {' → '.join(state['plan'])} (routeur {self.router.name})")

        engine = "langgraph" if HAS_LANGGRAPH else "python"
        if HAS_LANGGRAPH:
            limit = 4 * (len(state["plan"]) + 2)
            final = self._graph().invoke(state, config={"recursion_limit": limit})
        else:
            final = state
            while True:
                final = self._plan_node(final)
                if not final.get("next"):
                    break
                final = self._execute_node(final)

        self.gpu.suspend_ollama_models()
        self.gpu.empty_cuda_cache()
        steps = [StepRecord(**s) for s in final["steps"]]
        error = final.get("error")
        success = error is None and bool(final["ctx"].get(K.CLINICAL_REPORT))
        return RunResult(
            success=success,
            context=final["ctx"],
            steps=steps,
            plan=final["plan"],
            error=error if error or success else "Rapport clinique non produit",
            duration=time.perf_counter() - start,
            router=self.router.name,
            engine=engine,
        )

    def run_tool(self, name: str, ctx: Dict[str, Any]) -> Tuple[StepRecord, Dict[str, Any]]:
        """Exécute un seul outil (serveur MCP : tools/call)."""
        return self._run_one(TOOLS_BY_NAME[name], ctx)

    # --- Graphe ---------------------------------------------------------------
    def _graph(self):
        g = StateGraph(_State)
        g.add_node("plan", self._plan_node)
        g.add_node("execute", self._execute_node)
        g.set_entry_point("plan")
        g.add_conditional_edges("plan", lambda s: "execute" if s.get("next") else "end", {"execute": "execute", "end": END})
        g.add_edge("execute", "plan")
        return g.compile()

    def _plan_node(self, state: _State) -> _State:
        state = dict(state)  # type: ignore[assignment]
        state["next"] = []
        if state.get("error"):
            return state
        ctx = state["ctx"]
        goals = [g for g in self.planner.goals(ctx) if g not in state.get("waived", [])]
        try:
            remaining = self.planner.plan(ctx, goals)
        except PlanningError as e:
            state["error"] = str(e)
            return state
        if not remaining:
            return state  # objectifs atteints
        ready = Planner.ready(remaining, ctx, state["done"])
        if not ready:
            state["error"] = f"Pipeline bloqué : aucune étape exécutable parmi {[t.name for t in remaining]}"
            return state
        if len(ready) > 1 and not any(t.exclusive for t in ready):
            state["next"] = [t.name for t in ready]  # vague parallèle d'outils légers
        else:
            state["next"] = [self.router.choose(ready, ctx, state["done"]).name]
        return state

    def _execute_node(self, state: _State) -> _State:
        state = dict(state)  # type: ignore[assignment]
        tools = [TOOLS_BY_NAME[n] for n in state["next"]]
        ctx = state["ctx"]
        ctx[K.STEPS_COMPLETED] = [s["ui_step"] for s in state["steps"] if s["status"] != "failed"]
        ctx[K.EXECUTION_TIME] = round(sum(s["duration"] for s in state["steps"]), 1)
        ctx[K.ORCHESTRATION] = {
            "engine": "langgraph" if HAS_LANGGRAPH else "python",
            "router": self.router.name,
            "plan": list(state["plan"]),
        }

        if len(tools) == 1:
            outcomes = [self._run_one(tools[0], ctx)]
        else:
            snapshot = copy.deepcopy(ctx)
            with ThreadPoolExecutor(max_workers=min(self.max_parallel, len(tools))) as pool:
                outcomes = list(pool.map(lambda t: self._run_one(t, snapshot), tools))

        # Fusion dans l'ordre du registre : résultat indépendant de l'ordre d'exécution
        for record, produced in sorted(outcomes, key=lambda o: list(TOOLS_BY_NAME).index(o[0].tool)):
            state["steps"] = state["steps"] + [record.to_dict()]
            if record.status == "failed":
                tool = TOOLS_BY_NAME[record.tool]
                if tool.critical:
                    state["error"] = state.get("error") or record.error
                else:
                    logger.warning(f"[Orchestrator] {tool.name} (non critique) en échec : {record.error}")
                    state["waived"] = state.get("waived", []) + list(tool.produces)
                continue
            ctx.update(produced)
            state["done"] = state["done"] + [record.tool]
        state["ctx"] = ctx
        return state

    # --- Exécution d'un outil -------------------------------------------------
    def _notify(self, *args: Any) -> None:
        if self.on_step:
            try:
                self.on_step(*args)
            except Exception:  # un callback d'UI ne doit jamais casser le pipeline
                logger.exception("on_step callback")

    def _gpu_before(self, tool: ToolSpec) -> None:
        if tool.gpu_phase == GPU_PARABRICKS:
            self.gpu.prepare_for_parabricks()
        elif tool.gpu_phase == GPU_BIOGPT:
            self.gpu.prepare_for_biogpt()

    def _gpu_after(self, tool: ToolSpec, duration: float) -> None:
        if tool.gpu_phase == GPU_PARABRICKS:
            self.gpu.release_after_parabricks()
        if tool.gpu_phase:
            self.gpu.after_agent_step(tool.name, duration)

    def _run_one(self, tool: ToolSpec, ctx: Dict[str, Any]) -> Tuple[StepRecord, Dict[str, Any]]:
        self._notify(tool.ui_step, "running")
        key = self.cache.key(tool, ctx) if self.cache else None
        cached = self.cache.get(key) if self.cache else None
        if cached is not None:
            logger.info(f"[Orchestrator] {tool.name} : résultat réutilisé (cache {key[:12]})")
            self._notify(tool.ui_step, "completed", 0.0)
            return StepRecord(tool.name, tool.ui_step, "cached"), cached

        t0 = time.perf_counter()
        self._gpu_before(tool)
        try:
            result: AgentResult = tool.build_agent(self.config).run(ctx)
        finally:
            duration = time.perf_counter() - t0
            self._gpu_after(tool, duration)

        missing = [k for k in tool.produces if result.success and result.data.get(k) in (None, "")]
        if result.success and missing:
            result = AgentResult.fail(f"{tool.name} n'a pas produit {missing}")
        if not result.success:
            self._notify(tool.ui_step, "failed", duration)
            return StepRecord(tool.name, tool.ui_step, "failed", duration, result.error), {}

        if self.cache:
            self.cache.put(key, result.data)
        self._notify(tool.ui_step, "completed", duration)
        return StepRecord(tool.name, tool.ui_step, "completed", duration), result.data


def describe_plan(ctx: Dict[str, Any]) -> Sequence[str]:
    """Plan qui serait exécuté pour ce contexte (API / MCP, sans rien lancer)."""
    return [t.name for t in Planner().plan(K.validate_initial(dict(ctx)))]
