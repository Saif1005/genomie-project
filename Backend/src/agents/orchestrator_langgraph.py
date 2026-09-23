"""Orchestrateur LangGraph + LangChain + MCP — multi-agent intelligent."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict, Annotated

from loguru import logger

from src.agents.base_agent import BaseAgent, AgentResult, AgentStatus
from src.agents.langchain_prompts import ORCHESTRATOR_SYSTEM_PROMPT, ROUTER_PROMPT
from src.agents.langchain_tools import build_langchain_tools, context_summary
from src.mcp.bridge import MCPToolBridge
from src.report.clinical_report_builder import build_clinical_report
from src.schemas.pipeline import PipelineContext, PipelineState
from src.utils.gpu_manager import get_gpu_manager, assert_cuda_operational

try:
    from langchain_ollama import ChatOllama
    from langchain_core.messages import HumanMessage, SystemMessage
    from langgraph.graph import StateGraph, END

    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False
    ChatOllama = None
    StateGraph = None
    END = None


def _require_cuda() -> bool:
    """CUDA obligatoire ? Défaut : oui sur AWS (T4), non en local (fallback CPU autorisé)."""
    from config.deployment import is_local

    default = "false" if is_local() else "true"
    return os.getenv("REQUIRE_CUDA", default).lower() in ("1", "true", "yes")


class GraphState(TypedDict, total=False):
    context: Dict[str, Any]
    steps_done: List[str]
    last_error: Optional[str]
    next_tool: str
    finished: bool
    messages: List[Any]


def _parse_router_json(text: str) -> Dict[str, str]:
    try:
        m = re.search(r"\{[^{}]+\}", text, re.DOTALL)
        if m:
            data = json.loads(m.group())
            return {"next_tool": data.get("next_tool", "DONE"), "reason": data.get("reason", "")}
    except json.JSONDecodeError:
        pass
    return {"next_tool": "DONE", "reason": "parse_error"}


class OrchestratorLangGraph(BaseAgent):
    """Orchestrateur intelligent : LangGraph + Mistral Ollama + tools MCP."""

    DEFAULT_SEQUENCE = [
        "data_manager",
        "genomic_pipeline",
        "vcf_analysis",
        "llm_training",
        "prediction",
        "report",
    ]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("OrchestratorLangGraph", config)
        if not HAS_LANGGRAPH:
            raise ImportError(
                "LangGraph requis: pip install -r requirements-langchain.txt"
            )
        self.gpu = get_gpu_manager()
        if _require_cuda():
            assert_cuda_operational()
        self.bridge = MCPToolBridge(config)
        self.tools = build_langchain_tools(self.bridge)
        self._tool_map = {t.name: t for t in self.tools}
        model = os.getenv("ORCHESTRATOR_LLM_MODEL", "mistral:v0.3")
        self.llm = ChatOllama(
            model=model,
            base_url=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
            temperature=float(os.getenv("ORCHESTRATOR_TEMPERATURE", "0.1")),
        )

    def validate_input(self, context: Dict[str, Any]) -> bool:
        return bool(context.get("patient_id"))

    def _planned_sequence(self, ctx: PipelineContext) -> List[str]:
        if ctx.vcf_s3:
            return ["vcf_analysis", "prediction", "report"]
        seq = list(self.DEFAULT_SEQUENCE)
        if not ctx.train_llm:
            seq = [s for s in seq if s != "llm_training"]
        return seq

    def _router_node(self, state: GraphState) -> GraphState:
        ctx = PipelineContext.from_agent_dict(state["context"])
        seq = self._planned_sequence(ctx)
        done = set(state.get("steps_done", []))
        remaining = [s for s in seq if s not in done]

        if not remaining:
            return {**state, "next_tool": "DONE", "finished": True}

        if os.getenv("ORCHESTRATOR_DETERMINISTIC", "false").lower() in ("1", "true", "yes"):
            next_tool = remaining[0]
            logger.info(f"[Router] deterministic next={next_tool}")
            return {**state, "next_tool": next_tool, "finished": False}

        prompt = ROUTER_PROMPT.format(
            context_summary=context_summary(self.bridge),
            steps_done=list(done),
            last_error=state.get("last_error") or "none",
        )
        messages = [
            SystemMessage(content=ORCHESTRATOR_SYSTEM_PROMPT),
            HumanMessage(content=f"{prompt}\nTools restants suggérés: {remaining}"),
        ]
        try:
            resp = self.llm.invoke(messages)
            parsed = _parse_router_json(resp.content)
            next_tool = parsed["next_tool"]
            if next_tool not in self._tool_map and next_tool != "DONE":
                next_tool = remaining[0]
            logger.info(f"[Router] next={next_tool} reason={parsed.get('reason')}")
        except Exception as e:
            logger.error(f"Router LLM failed: {e}")
            if _require_cuda():
                raise
            next_tool = remaining[0]

        return {**state, "next_tool": next_tool, "finished": next_tool == "DONE"}

    def _tool_args(self, tool_name: str) -> Dict[str, Any]:
        ctx = self.bridge.get_context_dict()
        args: Dict[str, Any] = {"patient_id": ctx.get("patient_id")}
        if tool_name == "data_manager":
            args["fastq_r1"] = ctx.get("fastq_r1") or ctx.get("fastq_r1_s3")
            args["fastq_r2"] = ctx.get("fastq_r2") or ctx.get("fastq_r2_s3")
        elif tool_name == "genomic_pipeline":
            args["fastq_r1_s3"] = ctx.get("fastq_r1_s3")
            args["fastq_r2_s3"] = ctx.get("fastq_r2_s3")
        elif tool_name == "vcf_analysis":
            args["vcf_s3"] = ctx.get("vcf_s3")
        elif tool_name == "llm_training":
            args["train_llm"] = ctx.get("train_llm", False)
        return args

    def _tool_node(self, state: GraphState) -> GraphState:
        tool_name = state.get("next_tool", "")
        if tool_name == "DONE" or tool_name not in self._tool_map:
            return {**state, "finished": True}

        if tool_name == "genomic_pipeline":
            self.gpu.suspend_ollama_models()
            self.gpu.prepare_for_parabricks()
        elif tool_name == "prediction":
            self.gpu.prepare_for_biogpt()

        on_step = self.config.get("on_step")
        if on_step:
            on_step(tool_name, "running")

        try:
            raw = self._tool_map[tool_name].invoke(self._tool_args(tool_name))
            payload = json.loads(raw) if isinstance(raw, str) else raw
            success = payload.get("success", False)
            if payload.get("context"):
                state["context"] = payload["context"]
            steps = list(state.get("steps_done", []))
            exec_time = payload.get("execution_time")
            if success:
                steps.append(tool_name)
                if on_step:
                    on_step(tool_name, "completed", exec_time)
            elif on_step:
                on_step(tool_name, "failed", exec_time)
            return {
                **state,
                "steps_done": steps,
                "last_error": None if success else payload.get("error"),
                "finished": False,
            }
        except Exception as e:
            if on_step:
                on_step(tool_name, "failed")
            return {**state, "last_error": str(e), "finished": True}

    def _should_continue(self, state: GraphState) -> str:
        if state.get("finished") or state.get("last_error"):
            return "end"
        if state.get("next_tool") == "DONE":
            return "end"
        return "tool"

    def _build_graph(self):
        g = StateGraph(GraphState)
        g.add_node("router", self._router_node)
        g.add_node("tool", self._tool_node)
        g.set_entry_point("router")
        g.add_conditional_edges("router", self._should_continue, {"tool": "tool", "end": END})
        g.add_edge("tool", "router")
        return g.compile()

    def execute(self, context: Dict[str, Any]) -> AgentResult:
        start = datetime.now()
        self.bridge.init_context(context)
        self.gpu.log_transition("orchestrator_langgraph", "START")

        graph = self._build_graph()
        initial: GraphState = {
            "context": context,
            "steps_done": [],
            "last_error": None,
            "next_tool": "",
            "finished": False,
            "messages": [],
        }

        max_iters = int(os.getenv("LANGGRAPH_MAX_ITERATIONS", "20"))
        final_state = graph.invoke(initial, config={"recursion_limit": max_iters})

        self.gpu.suspend_ollama_models()
        self.gpu.empty_cuda_cache()

        ctx_dict = self.bridge.get_context_dict()
        ctx = PipelineContext.from_agent_dict(ctx_dict)
        elapsed = (datetime.now() - start).total_seconds()
        steps_done = final_state.get("steps_done", [])
        success = not final_state.get("last_error") and "report" in steps_done

        clinical_report = ctx_dict.get("clinical_report")
        if not clinical_report and success:
            clinical_report = build_clinical_report(
                ctx_dict,
                execution_time_seconds=elapsed,
                steps_completed=steps_done,
            ).to_api_dict()

        return AgentResult(
            success=success,
            status=AgentStatus.COMPLETED if success else AgentStatus.FAILED,
            data={
                "patient_id": ctx.patient_id,
                "pipeline_state": PipelineState.COMPLETED.value if success else PipelineState.FAILED.value,
                "steps_done": steps_done,
                "context": ctx_dict,
                "clinical_report": clinical_report,
                "vcf_s3": ctx.vcf_s3,
                "bam_s3": ctx.bam_recal_s3 or ctx.bam_s3,
                "results": ctx.prediction_results.model_dump() if ctx.prediction_results else {},
                "report_path": ctx.report_path,
                "report_s3": ctx.report_s3,
                "orchestrator": "langgraph+mcp",
            },
            error=final_state.get("last_error"),
            execution_time=elapsed,
        )
