"""Local engine: a run's audit trail (tool calls + saved stages) lives in the
local audit file, and get_funnel_audit / list_funnels read it back."""
import asyncio

import pytest

import novomcp.mcp.tools as tools
from novomcp.mcp.spine import FileAuditSink, LocalAuthGate, NoopMeter, Spine
from novomcp.mcp.tools import MCPToolExecutor

RUN = "fnl_test_egfr_001"


@pytest.fixture
def executor(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVO_AUTH", "local")
    monkeypatch.delenv("FUNNEL_BACKEND_URL", raising=False)
    ex = MCPToolExecutor(service_urls={}, internal_api_key="local")
    ex.spine = Spine(auth=LocalAuthGate(), meter=NoopMeter(), audit=FileAuditSink(str(tmp_path / "audit.jsonl")))
    return ex


def test_the_run_tools_are_listed_on_a_local_engine(monkeypatch):
    monkeypatch.setenv("NOVO_AUTH", "local")
    monkeypatch.delenv("FUNNEL_BACKEND_URL", raising=False)
    for name in ("save_funnel_stage", "get_funnel_audit", "list_funnels"):
        assert tools.is_tool_locally_available(name)
    assert not tools.is_tool_locally_available("save_funnel_memory")  # still needs the backend


async def _call(ex, tool, args):
    return await ex.execute(tool, args, org_id="local", user_id="local")


async def test_a_run_merges_tool_calls_and_saved_stages_in_order(executor):
    ex = executor
    assert (await _call(ex, "calculate_properties", {"smiles": "CCO", "funnel_id": RUN})).success
    saved = await _call(ex, "save_funnel_stage", {
        "funnel_id": RUN, "funnel_stage": 7, "stage_name": "lead_optimization",
        "molecules_in": 40, "molecules_out": 12, "molecules_filtered": {"compliance_block": 3, "user_removed": 25},
        "human_reviewed": True, "human_decision": "Keep the 12 with QED > 0.6", "human_prompt": "drop anything flagged",
    })
    assert saved.success and saved.data["stage_index"] == 1

    audit = await _call(ex, "get_funnel_audit", {"funnel_id": RUN})
    assert audit.success
    stages = audit.data["stages"]
    assert [st["event_type"] for st in stages] == ["exploration", "checkpoint"]
    explore, checkpoint = stages
    assert explore["tool_name"] == "calculate_properties"
    assert explore["tool_arguments"]["smiles"] == "CCO"
    assert explore["results_summary"]["lipinski_pass"] is True
    assert checkpoint["stage_label"] == "Lead Optimization"
    assert checkpoint["molecules_filtered"] == {"compliance_block": 3, "user_removed": 25}
    assert checkpoint["human_decision"] == "Keep the 12 with QED > 0.6"

    checkpoints_only = await _call(ex, "get_funnel_audit", {"funnel_id": RUN, "event_type": "checkpoint"})
    assert [st["stage_name"] for st in checkpoints_only.data["stages"]] == ["lead_optimization"]


async def test_stage_index_counts_checkpoints_per_run(executor):
    ex = executor
    for _ in range(2):
        await _call(ex, "save_funnel_stage", {"funnel_id": RUN, "stage_name": "review"})
    third = await _call(ex, "save_funnel_stage", {"funnel_id": RUN, "stage_name": "review"})
    other = await _call(ex, "save_funnel_stage", {"funnel_id": "fnl_other", "stage_name": "review"})
    assert third.data["stage_index"] == 3
    assert other.data["stage_index"] == 1


async def test_list_funnels_summarizes_each_run(executor):
    ex = executor
    await _call(ex, "calculate_properties", {"smiles": "CCO", "funnel_id": RUN})
    await _call(ex, "save_funnel_stage", {"funnel_id": RUN, "stage_name": "final_report", "stage_label": "Final report"})
    listed = await _call(ex, "list_funnels", {})
    assert listed.success
    run = next(f for f in listed.data["funnels"] if f["funnel_id"] == RUN)
    assert (run["tool_calls"], run["checkpoints"], run["last_stage"]) == (1, 1, "Final report")


async def test_an_unknown_run_is_not_found(executor):
    result = await _call(executor, "get_funnel_audit", {"funnel_id": "fnl_missing"})
    assert not result.success and "not found" in result.error


async def test_each_tool_call_appears_once_in_the_run(executor):
    ex = executor
    await _call(ex, "calculate_properties", {"smiles": "CCO", "funnel_id": RUN})
    await _call(ex, "calculate_properties", {"smiles": "CCN", "funnel_id": RUN})
    await _call(ex, "save_funnel_stage", {"funnel_id": RUN, "stage_name": "review"})
    await asyncio.sleep(0.05)  # let any background auto-log tasks finish first
    stages = (await _call(ex, "get_funnel_audit", {"funnel_id": RUN})).data["stages"]
    assert [st["event_type"] for st in stages] == ["exploration", "exploration", "checkpoint"]
