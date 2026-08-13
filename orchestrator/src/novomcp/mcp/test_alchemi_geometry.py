"""
Hermetic tests for the ALCHEMI geometry wiring:
- the `engine` axis on optimize_geometry_nnp (forwarded to novomcp-nnp)
- the batch_geometry_relaxation MCP tool (registration + batched proxy)

These do NOT hit novomcp-nnp. They construct the executor without __init__ and
monkeypatch `_call_service` to capture the payload, so they exercise exactly the
wrapper's own logic: param forwarding, endpoint selection, batch-size passthrough,
per-item passthrough, and input validation.
"""
import pytest

from novomcp.mcp.tools import (
    MCPToolExecutor,
    MCP_TOOLS,
    BATCH_TOOLS,
    COMPUTE_ONLY_TOOLS,
)


class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):
        return str(self._payload)


def _capturing_executor(payload_sink, response):
    """Executor without __init__; _call_service records (service, endpoint, data)."""
    ex = MCPToolExecutor.__new__(MCPToolExecutor)

    async def call(service, endpoint, data, method="POST", timeout=30.0, api_key=None):
        payload_sink["service"] = service
        payload_sink["endpoint"] = endpoint
        payload_sink["data"] = data
        payload_sink["timeout"] = timeout
        return response

    ex._call_service = call
    return ex


# ----- registration ------------------------------------------------------------------------------
def test_engine_axis_is_advertised():
    spec = MCP_TOOLS["optimize_geometry_nnp"]
    engine = spec["inputSchema"]["properties"]["engine"]
    assert engine["enum"] == ["ase", "alchemi"]
    assert engine["default"] == "ase"
    # method (the potential) and engine (the executor) stay separate axes
    assert "engine" not in spec["inputSchema"]["properties"]["method"]["enum"]


def test_batch_tool_is_registered():
    assert "batch_geometry_relaxation" in MCP_TOOLS
    spec = MCP_TOOLS["batch_geometry_relaxation"]
    assert spec["inputSchema"]["required"] == ["smiles_list"]
    assert spec["inputSchema"]["properties"]["engine"]["default"] == "alchemi"
    assert "batch_geometry_relaxation" in {t["name"] for t in MCP_TOOLS.values()}
    # per-item batch + compute-tier surface
    assert BATCH_TOOLS["batch_geometry_relaxation"] == "smiles_list"
    assert "batch_geometry_relaxation" in COMPUTE_ONLY_TOOLS


# ----- optimize_geometry_nnp engine forwarding ---------------------------------------------------
@pytest.mark.asyncio
async def test_engine_is_forwarded_to_service():
    sink = {}
    ex = _capturing_executor(sink, _FakeResp(200, {"energy": -1.0, "converged": True}))
    r = await ex._execute_optimize_geometry_nnp({"smiles": "CCO", "engine": "alchemi"})
    assert r.success, r.error
    assert sink["endpoint"] == "/api/optimize-geometry"
    assert sink["data"]["engine"] == "alchemi"


@pytest.mark.asyncio
async def test_engine_omitted_when_not_requested():
    sink = {}
    ex = _capturing_executor(sink, _FakeResp(200, {"energy": -1.0}))
    r = await ex._execute_optimize_geometry_nnp({"smiles": "CCO"})
    assert r.success, r.error
    assert "engine" not in sink["data"]  # default resolved service-side, not forced


# ----- batch_geometry_relaxation -----------------------------------------------------------------
@pytest.mark.asyncio
async def test_batch_proxies_full_list_in_one_call():
    sink = {}
    series = ["CCO", "CCCO", "CCCCO"]
    resp = _FakeResp(200, {"results": [{"id": i} for i in range(len(series))]})
    ex = _capturing_executor(sink, resp)
    r = await ex._execute_batch_geometry_relaxation({"smiles_list": series})
    assert r.success, r.error
    assert sink["endpoint"] == "/api/relax-batch"
    assert sink["data"]["smiles_list"] == series          # one batched call, not a loop
    assert sink["data"]["engine"] == "alchemi"            # batched path default
    assert r.usage["batch_size"] == 3


@pytest.mark.asyncio
async def test_batch_rejects_empty_input():
    ex = MCPToolExecutor.__new__(MCPToolExecutor)
    r = await ex._execute_batch_geometry_relaxation({})
    assert not r.success
    assert "smiles_list" in r.error


@pytest.mark.asyncio
async def test_batch_surfaces_service_error_honestly():
    sink = {}
    ex = _capturing_executor(sink, _FakeResp(503, "alchemi backend not built"))
    r = await ex._execute_batch_geometry_relaxation({"smiles_list": ["CCO"], "engine": "alchemi"})
    assert not r.success
    assert "503" in r.error  # no fallback data — the upstream state is reported
