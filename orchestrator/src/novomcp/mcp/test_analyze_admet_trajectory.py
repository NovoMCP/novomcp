"""
Hermetic tests for the analyze_admet_trajectory MCP tool wiring.

These do NOT hit the addie-models service or the full execute() dispatch (credits/auth/tiers).
They construct the executor without __init__ and monkeypatch `_call_service` with a fake addie
response, so they exercise exactly the wrapper's own logic: order preservation, flat-numeric
extraction, endpoint filtering, the fixed-endpoint contract (dropped endpoints reported, a failed
STEP is fatal), threshold pass-through, and input validation. The trajectory math itself is covered
by analysis/test_trajectory_diagnostic.py.
"""
import json

import pytest

from novomcp.mcp.tools import MCPToolExecutor, MCP_TOOLS


# ----- fakes -------------------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _executor(fake_call):
    """An MCPToolExecutor without __init__ (skips httpx/config/redis) + a fake _call_service."""
    ex = MCPToolExecutor.__new__(MCPToolExecutor)
    ex._call_service = fake_call
    return ex


# per-step trajectory shapes with clear margins so classes are threshold-robust
def _shapes(i, n):
    frozen = [0.10, 0.30, 0.55, 0.70][:1] + [min(0.10 + 0.20 * k, 0.70) for k in range(1, n)]
    return {
        "climber_probability": round(0.10 + (0.6 / (n - 1)) * i, 4),      # monotone up
        "descender_probability": round(0.90 - (0.6 / (n - 1)) * i, 4),    # monotone down
        "frozen_probability": round(frozen[i], 4),                        # rises then plateaus
        "flatline_probability": 0.40,                                     # never moves
        "note": "non-numeric-should-be-filtered",                         # must be dropped
    }


def _fake_addie(shapes=_shapes, shuffle=False, fail_index=None, drop_endpoint_at=None):
    async def call(service, endpoint, data, method="POST", timeout=30.0, api_key=None):
        assert service == "addie-models" and endpoint == "/addie/process"
        mols = data["molecules"]
        n = len(mols)
        results = []
        for m in mols:
            i = int(m["id"])
            if fail_index is not None and i == fail_index:
                results.append({"id": m["id"], "error": "model failed on this structure", "predictions": {}})
                continue
            preds = dict(shapes(i, n))
            if drop_endpoint_at is not None and i == drop_endpoint_at[0]:
                preds.pop(drop_endpoint_at[1], None)
            results.append({"id": m["id"], "predictions": preds})
        if shuffle:
            results = results[::-1]  # return out of order to prove reindex-by-id
        return _FakeResp(200, {"results": results})
    return call


SERIES = ["CCO", "CCCO", "CCCCO", "CCCCCO", "CCCCCCO", "CCCCCCCO", "CCCCCCCCO", "CCCCCCCCCO"]


# ----- registration ------------------------------------------------------------------------------
def test_tool_is_registered():
    assert "analyze_admet_trajectory" in MCP_TOOLS
    spec = MCP_TOOLS["analyze_admet_trajectory"]
    assert spec["inputSchema"]["required"] == ["smiles_series"]
    assert spec["inputSchema"]["properties"]["smiles_series"]["maxItems"] == 100
    assert "analyze_admet_trajectory" in {t["name"] for t in MCP_TOOLS.values()}


# ----- core behaviour ----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_classifies_series_and_filters_nonnumeric():
    ex = _executor(_fake_addie())
    r = await ex._execute_analyze_admet_trajectory({"smiles_series": SERIES})
    assert r.success, r.error
    axes = r.data["axes"]
    assert axes["climber_probability"]["class"] == "climbing"
    assert axes["descender_probability"]["class"] == "descending"
    assert axes["frozen_probability"]["class"] == "frozen"
    assert axes["flatline_probability"]["class"] == "flat"
    assert "note" not in axes                      # non-numeric field filtered out
    assert r.data["n_molecules"] == len(SERIES)
    assert r.data["dropped_endpoints"] == []
    assert r.usage["queries"] == len(SERIES)


@pytest.mark.asyncio
async def test_order_preserved_when_service_returns_shuffled():
    # A climbing endpoint must still read 'climbing' even if addie returns results out of order,
    # because we reindex by the id we assigned (index), not by response order.
    ex = _executor(_fake_addie(shuffle=True))
    r = await ex._execute_analyze_admet_trajectory({"smiles_series": SERIES})
    assert r.success
    assert r.data["axes"]["climber_probability"]["class"] == "climbing"
    assert r.data["axes"]["descender_probability"]["class"] == "descending"


@pytest.mark.asyncio
async def test_endpoint_subset():
    ex = _executor(_fake_addie())
    r = await ex._execute_analyze_admet_trajectory(
        {"smiles_series": SERIES, "endpoints": ["climber_probability", "flatline_probability"]})
    assert r.success
    assert set(r.data["axes"]) == {"climber_probability", "flatline_probability"}


@pytest.mark.asyncio
async def test_threshold_override_changes_classification():
    # flatline moves 0 -> always flat; make a tiny-but-nonzero mover and show flat_abs flips it.
    def tiny(i, n):
        # range ~0.07 over 8 steps: below default flat_abs (0.10) -> flat; above 0.05 -> climbing
        return {"tiny_probability": round(0.40 + 0.01 * i, 4)}
    ex = _executor(_fake_addie(shapes=tiny))
    default = await ex._execute_analyze_admet_trajectory({"smiles_series": SERIES})
    lowered = await ex._execute_analyze_admet_trajectory(
        {"smiles_series": SERIES, "thresholds": {"flat_abs": 0.05}})
    assert default.data["axes"]["tiny_probability"]["class"] == "flat"
    assert lowered.data["axes"]["tiny_probability"]["class"] == "climbing"


@pytest.mark.asyncio
async def test_dropped_endpoint_reported_not_fatal():
    # frozen_probability absent at step 3 -> dropped from the whole trajectory and named; the rest
    # still analyze (the fixed-endpoint contract from align_series).
    ex = _executor(_fake_addie(drop_endpoint_at=(3, "frozen_probability")))
    r = await ex._execute_analyze_admet_trajectory({"smiles_series": SERIES})
    assert r.success
    assert r.data["dropped_endpoints"] == ["frozen_probability"]
    assert "frozen_probability" not in r.data["axes"]
    assert "climber_probability" in r.data["axes"]


@pytest.mark.asyncio
async def test_failed_step_is_fatal():
    # A missing STEP (whole molecule failed) cannot be a hole in a trajectory -> hard error.
    ex = _executor(_fake_addie(fail_index=4))
    r = await ex._execute_analyze_admet_trajectory({"smiles_series": SERIES})
    assert not r.success
    assert "step 4" in r.error


@pytest.mark.asyncio
async def test_service_non_200_surfaced():
    async def bad(service, endpoint, data, method="POST", timeout=30.0, api_key=None):
        return _FakeResp(503, {"error": "addie down"})
    r = await _executor(bad)._execute_analyze_admet_trajectory({"smiles_series": SERIES})
    assert not r.success and "503" in r.error


# ----- validation --------------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("args, needle", [
    ({"smiles_series": ["CCO", "CCCO"]}, "at least 3"),
    ({"smiles_series": ["C"] * 101}, "capped at 100"),
    ({"smiles_series": "CCO"}, "list of SMILES"),
    ({"smiles_series": SERIES, "positions": [1, 2]}, "same length"),
    ({"smiles_series": SERIES, "endpoints": "herg"}, "list of endpoint"),
])
async def test_input_validation(args, needle):
    # validation happens before any service call, so a fake that would explode proves we never reach it
    async def boom(*a, **k):
        raise AssertionError("service should not be called on invalid input")
    r = await _executor(boom)._execute_analyze_admet_trajectory(args)
    assert not r.success and needle in r.error
