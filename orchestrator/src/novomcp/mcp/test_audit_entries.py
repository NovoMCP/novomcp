"""Local audit entries record what was run and what came back, so a run can be
reviewed, exported and repeated."""
import json

import httpx
import pytest

import novomcp.mcp.tools as tools
from novomcp.mcp.spine import FileAuditSink, Spine, LocalAuthGate, NoopMeter
from novomcp.mcp.tools import MCPToolExecutor


def test_arguments_keep_what_repeats_the_call_and_drop_secrets():
    args = {
        "smiles": "CCO",
        "context": {"intended_use": "research", "jurisdiction": "US"},
        "api_key": "sk-live-123",
        "Authorization": "Bearer abc",
    }
    out = tools._audit_arguments(args)
    assert out["smiles"] == "CCO"
    assert out["context"] == {"intended_use": "research", "jurisdiction": "US"}
    assert out["api_key"] == "<redacted>"
    assert out["Authorization"] == "<redacted>"


def test_oversized_arguments_are_summarized():
    big = ["C" * 40] * 500
    out = tools._audit_arguments({"smiles_list": big})
    assert out["smiles_list"]["_list_summary"] is True
    assert out["smiles_list"]["length"] == 500


def test_compliance_outcome_reads_the_nested_report():
    data = {
        "smiles": "C",
        "context": {"intended_use": "research", "jurisdiction": "US"},
        "compliance": {
            "overall_status": "STOP",
            "base_compliance": {"screen_flags": ["DEA-II"], "screen_flag_count": 1},
        },
    }
    assert tools._audit_outcome("check_compliance", data) == {"status": "STOP", "flags": ["DEA-II"]}


def test_compliance_outcome_accepts_a_comma_separated_flag_string():
    data = {"compliance": {"status": "BLOCKED", "screen_flags": "DEA-I, CWC-1"}}
    assert tools._audit_outcome("check_compliance", data) == {"status": "BLOCKED", "flags": ["DEA-I", "CWC-1"]}


def test_other_tools_fall_back_to_top_level_scalars():
    data = {"answer": 42, "label": "ok", "nested": {"x": 1}, "rows": [1, 2]}
    assert tools._audit_outcome("some_tool", data) == {"answer": 42, "label": "ok"}


@pytest.fixture
def executor(monkeypatch, tmp_path):
    monkeypatch.setenv("NOVO_AUTH", "local")
    monkeypatch.delenv("FUNNEL_BACKEND_URL", raising=False)
    path = tmp_path / "audit.jsonl"
    ex = MCPToolExecutor(service_urls={}, internal_api_key="local")
    ex.spine = Spine(auth=LocalAuthGate(), meter=NoopMeter(), audit=FileAuditSink(str(path)))
    return ex, path


def _last_entry(path):
    return json.loads(path.read_text().strip().split("\n")[-1])["payload"]


async def test_a_tool_call_writes_arguments_and_outcome(executor):
    ex, path = executor
    result = await ex.execute("calculate_properties", {"smiles": "CCO"}, org_id="local", user_id="local")
    assert result.success
    entry = _last_entry(path)
    assert entry["tool"] == "calculate_properties"
    assert entry["arguments"]["smiles"] == "CCO"
    assert entry["outcome"]["lipinski_pass"] is True


async def test_a_compliance_check_writes_its_verdict(executor, monkeypatch):
    ex, path = executor
    monkeypatch.setitem(ex.service_urls, "compliance", "https://compliance.example")

    async def fake_call(service, endpoint, data, method="POST", timeout=30.0, api_key=None):
        assert (service, endpoint) == ("compliance", "/api/check")
        return httpx.Response(200, json={"overall_status": "STOP", "base_compliance": {"screen_flags": ["DEA-II"]}})

    monkeypatch.setattr(ex, "_call_service", fake_call, raising=False)
    context = {"intended_use": "research", "jurisdiction": "US"}
    result = await ex.execute(
        "check_compliance", {"smiles": "CCC(=O)N(c1ccccc1)C1CCN(CCc2ccccc2)CC1", "context": context},
        org_id="local", user_id="local",
    )
    assert result.success
    entry = _last_entry(path)
    assert entry["arguments"]["context"] == context
    assert entry["outcome"] == {"status": "STOP", "flags": ["DEA-II"]}
