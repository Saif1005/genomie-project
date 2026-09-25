"""Orchestration multi-agent : planification dynamique, exécution, cache, parallélisme, routeur."""

import threading
import time

import pytest

from src.core import context as K
from src.core.agent import AgentResult, BaseAgent
from src.orchestration.cache import ResultCache
from src.orchestration.engine import Orchestrator, _NoGPU
from src.orchestration.planner import Planner, PlanningError
from src.orchestration.registry import TOOLS, ToolSpec
from src.orchestration.router import LLMRouter


def names(plan):
    return [t.name for t in plan]


# --- Planificateur (registre réel) -------------------------------------------
def test_vcf_input_skips_alignment():
    ctx = {K.PATIENT_ID: "P1", K.VCF_URI: "/data/p.vcf"}
    assert names(Planner().plan(ctx)) == ["variant_annotation", "vcf_analysis", "prediction", "report"]


def test_fastq_input_plans_full_pipeline():
    ctx = {K.PATIENT_ID: "P1", K.FASTQ_R1: "/a_R1.fq.gz", K.FASTQ_R2: "/a_R2.fq.gz"}
    assert names(Planner().plan(ctx)) == [
        "data_manager", "genomic_pipeline", "variant_annotation", "vcf_analysis", "prediction", "report",
    ]


def test_train_llm_adds_optional_agent():
    ctx = {K.PATIENT_ID: "P1", K.VCF_URI: "/data/p.vcf", K.TRAIN_LLM: True}
    assert names(Planner().plan(ctx))[-1] == "llm_training"


def test_resuming_from_intermediate_result_plans_only_remaining_steps():
    ctx = {K.PATIENT_ID: "P1", K.ANNOTATED_VARIANTS: "/x.json"}
    assert names(Planner().plan(ctx)) == ["vcf_analysis", "prediction", "report"]


def test_no_input_is_a_planning_error():
    with pytest.raises(PlanningError):
        Planner().plan({K.PATIENT_ID: "P1"})


def test_registry_is_consistent():
    produced = {k for t in TOOLS for k in t.produces}
    inputs = {K.PATIENT_ID, K.FASTQ_R1, K.FASTQ_R2, K.VCF_URI}
    for t in TOOLS:
        assert set(t.requires) <= produced | inputs, t.name
        assert t.input_schema()["required"] == list(t.requires)


# --- Moteur avec outils factices -----------------------------------------------
CALLS = []


class Echo(BaseAgent):
    """Produit « <outil>_out » ; échoue si config['fail'] contient son nom."""

    tool = ""

    def __init__(self, config=None):
        super().__init__("Echo", config)

    def execute(self, context):
        tool = self.tool
        CALLS.append(tool)
        if tool in self.config.get("fail", ()):
            return AgentResult.fail(f"{tool} en échec")
        if tool in self.config.get("slow", ()):
            time.sleep(0.2)
        out = {f"{tool}_out": f"{tool}:{threading.current_thread().name}"}
        if tool == "report":
            out[K.CLINICAL_REPORT] = {"ok": True}
        return AgentResult.ok(**out)


def __getattr__(name):
    """Une classe d'agent par outil factice : tests.unit.test_orchestration:Echo_<outil>."""
    if name.startswith("Echo_"):
        return type(name, (Echo,), {"tool": name[5:]})
    raise AttributeError(name)


def fake_tool(name, requires, produces, **kw):
    return ToolSpec(name=name, label=name, description=name, agent=f"tests.unit.test_orchestration:Echo_{name}",
                    requires=requires, produces=produces, ui_step=name, **kw)


def make_engine(tools, **config):
    from src.orchestration import engine as E

    E.TOOLS_BY_NAME.clear()
    E.TOOLS_BY_NAME.update({t.name: t for t in tools})
    return Orchestrator(config=config, planner=Planner(tools), cache=config.pop("cache", None), gpu=_NoGPU())


@pytest.fixture(autouse=True)
def restore_registry():
    from src.orchestration import engine as E

    saved = dict(E.TOOLS_BY_NAME)
    CALLS.clear()
    yield
    E.TOOLS_BY_NAME.clear()
    E.TOOLS_BY_NAME.update(saved)


LINEAR = [
    fake_tool("a", (K.PATIENT_ID, K.VCF_URI), ("a_out",)),
    fake_tool("report", ("a_out",), ("report_out", K.CLINICAL_REPORT)),
]


def test_engine_runs_plan_and_reports_steps():
    result = make_engine(LINEAR).run({K.PATIENT_ID: "P1", K.VCF_URI: "/v.vcf"})
    assert result.success, result.error
    assert result.steps_completed == ["a", "report"]
    assert result.context["orchestration"]["plan"] == ["a", "report"]


def test_engine_stops_at_first_critical_failure():
    result = make_engine(LINEAR, fail=("a",)).run({K.PATIENT_ID: "P1", K.VCF_URI: "/v.vcf"})
    assert not result.success
    assert "a en échec" in result.error
    assert CALLS == ["a"]


def test_non_critical_failure_does_not_fail_the_analysis():
    tools = LINEAR + [fake_tool("extra", ("a_out",), ("extra_out",), critical=False, exclusive=True)]
    engine = make_engine(tools, fail=("extra",))
    engine.planner.goals = lambda ctx: [K.CLINICAL_REPORT, "extra_out"]
    result = engine.run({K.PATIENT_ID: "P1", K.VCF_URI: "/v.vcf"})
    assert result.success
    assert [s.status for s in result.steps if s.tool == "extra"] == ["failed"]


def test_independent_light_tools_run_in_parallel_and_merge_deterministically():
    tools = [
        fake_tool("x", (K.PATIENT_ID, K.VCF_URI), ("x_out",), exclusive=False),
        fake_tool("y", (K.PATIENT_ID, K.VCF_URI), ("y_out",), exclusive=False),
        fake_tool("report", ("x_out", "y_out"), ("report_out", K.CLINICAL_REPORT)),
    ]
    t0 = time.perf_counter()
    result = make_engine(tools, slow=("x", "y")).run({K.PATIENT_ID: "P1", K.VCF_URI: "/v.vcf"})
    assert result.success
    assert time.perf_counter() - t0 < 0.39  # 2 × 0,2 s en parallèle, pas en série
    assert [s.tool for s in result.steps] == ["x", "y", "report"]


def test_cached_tool_is_not_rerun(tmp_path):
    input_file = tmp_path / "in.fq"
    input_file.write_text("@r\nA\n+\nI\n")
    tools = [
        fake_tool("costly", (K.PATIENT_ID, K.VCF_URI), ("costly_out",), cache_inputs=(K.VCF_URI,)),
        fake_tool("report", ("costly_out",), ("report_out", K.CLINICAL_REPORT)),
    ]
    cache = ResultCache(tmp_path / "cache")
    ctx = {K.PATIENT_ID: "P1", K.VCF_URI: str(input_file)}
    make_engine(tools, cache=cache).run(dict(ctx))
    CALLS.clear()
    second = make_engine(tools, cache=cache).run(dict(ctx))
    assert CALLS == ["report"]
    assert second.steps[0].status == "cached"

    input_file.write_text("@r\nC\n+\nI\n@r2\nG\n+\nI\n")  # entrée modifiée → recalcul
    CALLS.clear()
    make_engine(tools, cache=cache).run(dict(ctx))
    assert CALLS == ["costly", "report"]


def test_llm_router_falls_back_on_invalid_answer(monkeypatch):
    router = LLMRouter.__new__(LLMRouter)
    router.client = type("C", (), {"generate": lambda self, *a, **k: "je ne sais pas"})()
    a, b = LINEAR[0], fake_tool("b", (), ())
    assert router.choose([a, b], {}, []) is a
    router.client = type("C", (), {"generate": lambda self, *a, **k: '{"next_tool": "b"}'})()
    assert router.choose([a, b], {}, []) is b
