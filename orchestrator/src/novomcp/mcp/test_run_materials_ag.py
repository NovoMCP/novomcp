"""Tests for run_materials_ag (mgm) — the materials autonomous-mode trigger.

The executor uses no instance state, so it's called with self=None. No network:
it only reads env + the in-module MCP_PROMPT_TEMPLATES.
"""
import asyncio

from novomcp.mcp.tools import (
    MCPToolExecutor,
    MCP_TOOLS,
    SHARED_TOOLS,
    FUNNEL_AUTOLOG_SKIP,
    is_tool_locally_available,
)


def _run(args):
    return asyncio.run(MCPToolExecutor._execute_run_materials_ag(None, args))


def test_registered_and_always_available():
    assert "run_materials_ag" in MCP_TOOLS
    assert "run_materials_ag" in SHARED_TOOLS          # both surfaces
    assert "run_materials_ag" in FUNNEL_AUTOLOG_SKIP    # meta trigger, not an event
    assert is_tool_locally_available("run_materials_ag")  # no gating — always visible


def test_missing_application_errors():
    r = _run({})
    assert not r.success


def test_catalyst_setup_required_without_stack(monkeypatch):
    monkeypatch.delenv("NOVOMCP_QM_URL", raising=False)
    monkeypatch.delenv("NOVOMCP_NNP_URL", raising=False)
    r = _run({"application": "co2_reduction_catalyst"})
    assert r.success
    assert r.data["status"] == "setup_required"
    assert "catalyst" in r.data["manual_workflow_hint"]


def test_catalyst_protocol_with_stack(monkeypatch):
    monkeypatch.setenv("NOVOMCP_QM_URL", "http://qm")
    monkeypatch.setenv("NOVOMCP_NNP_URL", "http://nnp")
    r = _run({"application": "co2_reduction_catalyst"})
    assert r.success
    assert r.data["domain"] == "catalyst"
    instr = r.data["instructions"]
    assert "Target reaction: co2_reduction" in instr   # {target_reaction} substituted
    assert "Substrate: O=C=O" in instr                 # reaction→substrate map
    assert "{target_reaction}" not in instr            # no leftover placeholder
    assert "{substrate}" not in instr
    assert "GO / CAUTION / STOP" in instr              # verdict framing
    assert "ANI-2x is organics-only" in instr          # metal method caveat


def test_catalyst_unknown_reaction_prompts_for_substrate(monkeypatch):
    monkeypatch.setenv("NOVOMCP_QM_URL", "http://qm")
    monkeypatch.setenv("NOVOMCP_NNP_URL", "http://nnp")
    r = _run({"application": "some_novel_reaction_catalyst"})
    assert r.success
    assert r.data["domain"] == "catalyst"
    assert "(specify the substrate SMILES)" in r.data["instructions"]


def test_setup_required_without_stack(monkeypatch):
    monkeypatch.delenv("NOVOMCP_QM_URL", raising=False)
    monkeypatch.delenv("NOVOMCP_NNP_URL", raising=False)
    r = _run({"application": "oled_blue"})
    assert r.success
    assert r.data["status"] == "setup_required"
    envs = {m["env_var"] for m in r.data["missing_services"]}
    assert {"NOVOMCP_QM_URL", "NOVOMCP_NNP_URL"} <= envs
    assert "oled" in r.data["manual_workflow_hint"]


def test_oled_protocol_with_stack(monkeypatch):
    monkeypatch.setenv("NOVOMCP_QM_URL", "http://qm")
    monkeypatch.setenv("NOVOMCP_NNP_URL", "http://nnp")
    r = _run({"application": "oled_blue"})
    assert r.success
    assert r.data["domain"] == "oled"
    instr = r.data["instructions"]
    assert "Emission target: blue" in instr        # {emission_target} substituted
    assert "{emission_target}" not in instr        # no leftover placeholder
    assert "GO / CAUTION / STOP" in instr          # verdict framing from the preamble


def test_electrolyte_window_mapped(monkeypatch):
    monkeypatch.setenv("NOVOMCP_QM_URL", "http://qm")
    monkeypatch.setenv("NOVOMCP_NNP_URL", "http://nnp")
    r = _run({"application": "li_ion_electrolyte"})
    assert r.success
    assert r.data["domain"] == "electrolyte"
    assert "Voltage window: standard_li_ion" in r.data["instructions"]


def test_smiles_list_substituted(monkeypatch):
    monkeypatch.setenv("NOVOMCP_QM_URL", "http://qm")
    monkeypatch.setenv("NOVOMCP_NNP_URL", "http://nnp")
    r = _run({"application": "oled_tadf", "smiles_list": ["c1ccccc1", "CCO"]})
    assert "c1ccccc1, CCO" in r.data["instructions"]
