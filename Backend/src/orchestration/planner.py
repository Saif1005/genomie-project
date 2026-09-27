"""Dynamic planner: backward chaining from the goals to the available data.

Deterministic (registry order breaks ties): the same context always gives the same plan.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from src.core import context as K
from src.orchestration.registry import TOOLS, ToolSpec


class PlanningError(RuntimeError):
    """No sequence of tools can reach the goal with the data provided."""


def _available(ctx: Dict[str, Any], key: str) -> bool:
    return ctx.get(key) not in (None, "", [], {})


class Planner:
    def __init__(self, tools: Sequence[ToolSpec] = TOOLS):
        self.tools = tuple(tools)

    def goals(self, ctx: Dict[str, Any]) -> List[str]:
        goals = [K.CLINICAL_REPORT]
        goals += [k for t in self.tools if t.name == "llm_training" and t.enabled(ctx) for k in t.produces]
        return goals

    def _producer(self, key: str, ctx: Dict[str, Any], visiting: Set[str]) -> Optional[ToolSpec]:
        for t in self.tools:
            if key in t.produces and t.enabled(ctx) and t.name not in visiting:
                return t
        return None

    def _resolve(self, key: str, ctx: Dict[str, Any], plan: List[ToolSpec], visiting: Set[str]) -> None:
        if _available(ctx, key) or any(key in t.produces for t in plan):
            return
        tool = self._producer(key, ctx, visiting)
        if tool is None:
            raise PlanningError(f"Cannot obtain \"{key}\": no data or tool available")
        visiting.add(tool.name)
        for dep in tool.requires:
            self._resolve(dep, ctx, plan, visiting)
        visiting.discard(tool.name)
        if tool not in plan:
            plan.append(tool)

    def plan(self, ctx: Dict[str, Any], goals: Optional[Iterable[str]] = None) -> List[ToolSpec]:
        plan: List[ToolSpec] = []
        for goal in goals or self.goals(ctx):
            self._resolve(goal, ctx, plan, set())
        return plan

    @staticmethod
    def ready(plan: Sequence[ToolSpec], ctx: Dict[str, Any], done: Iterable[str]) -> List[ToolSpec]:
        """Plan tools not yet run whose inputs are all available."""
        done = set(done)
        return [t for t in plan if t.name not in done and all(_available(ctx, k) for k in t.requires)]
