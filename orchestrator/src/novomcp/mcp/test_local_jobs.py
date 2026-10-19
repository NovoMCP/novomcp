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
                                                      "estimated_runtime_minutes": 12}),
                                 "/status/gro_181L-ligand_1": (200, {"status": "processing", "step": "repair"})},
                   calls)
    res = asyncio.run(ex._execute_run_molecular_dynamics(
        {"smiles": "c1ccccc1", "pdb_id": "181l", "duration_ns": 1, "intent": "pose_stability",
         "adaptive_equilibration": True}))
    assert res.success
    sent = calls[0][2]
    assert sent["intent"] == "pose_stability" and sent["adaptive_equilibration"] is True
    assert sent["pdb_id"] == "181L"


def _submit(monkeypatch, statuses):
    """Submit 6LU7 with /status answering from `statuses` in turn (the last one repeats)."""
    calls, seq = [], list(statuses)
    ex = _executor(monkeypatch, {"/simulate": (200, {"job_id": "gro_6LU7-ligand_1", "status": "queued"})}, calls)
    inner = ex._call_service

    async def fake_call(service, endpoint, data, method="POST", timeout=30.0, api_key=None):
        if endpoint.startswith("/status/"):
            calls.append((service, endpoint, data))
            return httpx.Response(200, json=seq.pop(0) if len(seq) > 1 else seq[0])
        return await inner(service, endpoint, data, method=method, timeout=timeout)

    monkeypatch.setattr(ex, "_call_service", fake_call, raising=False)
    monkeypatch.setattr(ex, "_LOCAL_MD_INTAKE_POLL_S", 0.01, raising=False)
    monkeypatch.setattr(ex, "_LOCAL_MD_INTAKE_WAIT_S", 0.2, raising=False)
    res = asyncio.run(ex._execute_run_molecular_dynamics(
        {"smiles": "CC(=O)N", "pdb_id": "6lu7", "duration_ns": 1}))
    return res, [c[1] for c in calls if c[1].startswith("/status/")]


def test_refusal_is_returned_at_submission(local, monkeypatch):
    refusal = {"status": "refused", "result": {
        "primary_reason": "covalent ligand: Cys145 SG to ligand C (1.79 A)",
        "reasons": ["covalent ligand"], "suggested_branch": "covalent_ligand"}}
    res, polls = _submit(monkeypatch, [{"status": "queued", "step": "queued"},
                                       {"status": "processing", "step": "fetch_pdb"},
                                       {"status": "processing", "step": "classify"}, refusal])
    assert res.success and res.data["status"] == "refused" and res.data["refused"]
    assert res.data["suggested_branch"] == "covalent_ligand"
    assert "no simulation is running" in res.data["message"]
    assert len(polls) == 4


def test_failure_during_intake_is_returned_at_submission(local, monkeypatch):
    res, _ = _submit(monkeypatch, [{"status": "failed", "message": "Simulation failed: PDB 6LU7 not found"}])
    assert not res.success and "not found" in res.error


def test_accepted_job_returns_once_past_intake(local, monkeypatch):
    res, polls = _submit(monkeypatch, [{"status": "processing", "step": "classify"},
                                       {"status": "processing", "step": "repair"}])
    assert res.success and res.data["status"] == "queued" and "submitted" in res.data["message"]
    assert len(polls) == 2


def test_slow_intake_does_not_block_submission(local, monkeypatch):
    res, polls = _submit(monkeypatch, [{"status": "processing", "step": "classify"}])
    assert res.success and res.data["status"] == "queued"
    assert 1 < len(polls) <= 25  # gave up after the wait


def test_ligand_only_job_returns_once_preparing(local, monkeypatch):
    res, polls = _submit(monkeypatch, [{"status": "queued", "step": "queued"},
                                       {"status": "processing", "step": "preparation"}])
    assert res.success and res.data["status"] == "queued" and len(polls) == 2


@pytest.mark.parametrize("ns,ok", [(0.05, False), (0.1, True), (100, True), (150, False)])
def test_duration_bounds(local, monkeypatch, ns, ok):
    calls = []
    ex = _executor(monkeypatch, {"/simulate": (200, {"job_id": "gro_mol-1", "status": "queued"}),
                                 "/status/gro_mol-1": (200, {"status": "processing", "step": "preparation"})},
                   calls)
    res = asyncio.run(ex._execute_run_molecular_dynamics({"smiles": "CCO", "duration_ns": ns}))
    assert res.success is ok
    if not ok:
        assert "between 0.1 and 100" in res.error and not calls


COVALENT_AUDIT = {
    "status": "audited", "would_route_to": "refused", "suggested_branch": "covalent_ligand",
    "primary_reason": "covalent ligand bonded to the protein (PJE C5 C20 - CYS A145 SG (1.79 A)): "
                      "3 covalent ligand links",
    "reasons": ["covalent ligand bonded to the protein ..."],
    "profile": {"covalent_links": ["PJE C5 C20 - CYS A145 SG (1.79 A)"], "metal_sites": []},
}


def _audit_executor(monkeypatch, response, calls):
    ex = _executor(monkeypatch, {"/audit": response}, calls)

    async def no_redis():
        return None
    monkeypatch.setattr(ex, "_get_redis", no_redis, raising=False)
    return ex


def test_audit_asks_the_md_service_that_gates_submissions(local, monkeypatch):
    calls = []
    ex = _audit_executor(monkeypatch, (200, COVALENT_AUDIT), calls)
    res = asyncio.run(ex._execute_audit_system({"pdb_id": "6lu7"}))
    assert calls[0][:2] == ("gromacs-md", "/audit") and calls[0][2]["pdb_id"] == "6LU7"
    assert res.success and res.data["would_route_to"] == "refused"
    assert res.data["suggested_branch"] == "covalent_ligand"


def test_audit_falls_back_to_the_in_process_classifier(local, monkeypatch):
    import sys
    import types

    class Decision:
        route, suggested_branch, reasons = "run_soluble", None, ["happy path"]

        class profile:
            @staticmethod
            def model_dump():
                return {"metal_sites": []}

    async def classify_structure(**kwargs):
        return Decision()

    monkeypatch.setitem(sys.modules, "novomcp.core.intake",
                        types.SimpleNamespace(classify_structure=classify_structure))

    class FakeRCSB:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, url): return httpx.Response(200, text="ATOM\n")
    monkeypatch.setattr(httpx, "AsyncClient", FakeRCSB)

    ex = _audit_executor(monkeypatch, (503, {"detail": "down"}), [])
    res = asyncio.run(ex._execute_audit_system({"pdb_id": "181l"}))
    assert res.success and res.data["would_route_to"] == "run_soluble"


def test_running_md_status_relays_the_finish_time(local, monkeypatch):
    running = {"status": "processing", "progress": 87, "message": "Production MD (10.0ns): 8.84 ns done",
               "estimated_remaining_minutes": 26, "estimated_total_minutes": 236, "elapsed_minutes": 210,
               "estimated_completion_at": "2026-10-18T23:54:00+00:00"}
    ex = _executor(monkeypatch, {"/status/gro_2RH1-ligand_1": (200, running)}, [])
    res = asyncio.run(ex._execute_get_job_status({"job_id": "gro_2RH1-ligand_1"}))
    assert res.success and res.data["estimated_completion_at"] == "2026-10-18T23:54:00+00:00"
    assert "Estimated finish: 2026-10-18T23:54:00+00:00 (UTC)" in res.data["message"]
    assert "user's current time zone" in res.data["message"]

    del running["estimated_completion_at"]  # an older gromacs-md: no finish time, no hint
    res = asyncio.run(ex._execute_get_job_status({"job_id": "gro_2RH1-ligand_1"}))
    assert "estimated_completion_at" not in res.data and "Estimated finish" not in res.data["message"]
    assert "~26 minutes remaining" in res.data["message"]
