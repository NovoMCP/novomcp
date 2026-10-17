"""Local engine (NOVO_AUTH=local): MD jobs can be polled, cancelled and given an intent."""
import asyncio

import httpx
import pytest

import novomcp.mcp.tools as tools
from novomcp.mcp.tools import MCPToolExecutor


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("NOVO_AUTH", "local")
    monkeypatch.delenv("FUNNEL_BACKEND_URL", raising=False)
    monkeypatch.delenv("NOVOMCP_SHOW_HIDDEN_TOOLS", raising=False)


def _executor(monkeypatch, responses, calls):
    ex = MCPToolExecutor.__new__(MCPToolExecutor)
    ex.platform_version, ex.tools_updated = "test", "test"  # read by the next-tool hint

    async def fake_call(service, endpoint, data, method="POST", timeout=30.0, api_key=None):
        calls.append((service, endpoint, data))
        status, body = responses[endpoint]
        return httpx.Response(status, json=body)

    monkeypatch.setattr(ex, "_call_service", fake_call, raising=False)
    return ex


def test_job_tools_are_listed_on_a_local_engine(local):
    assert tools.is_tool_locally_available("get_job_status")
    assert tools.is_tool_locally_available("cancel_job")
    assert not tools.is_tool_locally_available("list_jobs")  # needs the backend


def test_job_tools_still_need_the_backend_when_hosted(monkeypatch):
    monkeypatch.setenv("NOVO_AUTH", "oauth")
    monkeypatch.delenv("FUNNEL_BACKEND_URL", raising=False)
    monkeypatch.delenv("NOVOMCP_SHOW_HIDDEN_TOOLS", raising=False)
    assert not tools.is_tool_locally_available("get_job_status")
    monkeypatch.setenv("FUNNEL_BACKEND_URL", "http://backend")
    assert tools.is_tool_locally_available("get_job_status")


def test_cancel_goes_to_gromacs_md(local, monkeypatch):
    calls = []
    ex = _executor(monkeypatch, {"/cancel/gro_x_1": (200, {"job_id": "gro_x_1", "status": "cancelled",
                                                          "cancelled": True, "killed_processes": 3})}, calls)
    res = asyncio.run(ex._execute_cancel_job({"job_id": "gro_x_1"}))
    assert res.success and res.data["cancelled"] and res.data["status"] == "cancelled"
    assert calls == [("gromacs-md", "/cancel/gro_x_1", {})]


def test_cancel_unknown_and_non_md_jobs(local, monkeypatch):
    ex = _executor(monkeypatch, {"/cancel/gro_missing": (404, {"detail": "not found"})}, [])
    assert "not found" in asyncio.run(ex._execute_cancel_job({"job_id": "gro_missing"})).error
    assert "MD jobs" in asyncio.run(ex._execute_cancel_job({"job_id": "qm_123"})).error


def test_md_submission_forwards_intent_and_adaptive_equilibration(local, monkeypatch):
    calls = []
    ex = _executor(monkeypatch, {"/simulate": (200, {"job_id": "gro_181L-ligand_1", "status": "queued",
                                                      "estimated_runtime_minutes": 12})}, calls)
    res = asyncio.run(ex._execute_run_molecular_dynamics(
        {"smiles": "c1ccccc1", "pdb_id": "181l", "duration_ns": 1, "intent": "pose_stability",
         "adaptive_equilibration": True}))
    assert res.success
    sent = calls[0][2]
    assert sent["intent"] == "pose_stability" and sent["adaptive_equilibration"] is True
    assert sent["pdb_id"] == "181L"
