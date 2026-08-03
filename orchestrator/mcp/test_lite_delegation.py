"""Drift-guard tests for the novomcp-lite delegation.

The in-process cheminformatics + public-API search primitives are sourced from
the open-source novomcp-lite package (`novomcp_tools`). These tests assert the
engine actually delegates to it — so the engine and the OSS subset share ONE
implementation and cannot silently drift back to an inlined copy.
"""
import asyncio

import pytest

import mcp.tools as tools
from mcp.tools import MCPToolExecutor
import novomcp_tools.chem as lite_chem
import novomcp_tools.search as lite_search

ASPIRIN = "CC(=O)OC1=CC=CC=C1C(=O)O"


def test_primitive_bindings_point_at_lite():
    """If someone re-inlines the logic, these identities break — the guard."""
    assert tools._lite_compute_properties is lite_chem.compute_properties
    assert tools._lite_compute_sa_score is lite_chem.compute_sa_score
    assert tools._lite_search is lite_search


def test_sa_score_delegates(monkeypatch):
    monkeypatch.setattr(tools, "_lite_compute_sa_score", lambda s: 4.2)
    assert tools._compute_sa_score("CCO") == 4.2


def test_basic_properties_delegates(monkeypatch):
    sentinel = {"molecular_weight": 1.0, "logp": 2.0}
    monkeypatch.setattr(tools, "_lite_compute_properties", lambda s: sentinel)
    ex = MCPToolExecutor.__new__(MCPToolExecutor)
    out = asyncio.run(ex._compute_basic_properties(ASPIRIN))
    assert out is sentinel


def test_golden_values_via_lite():
    """The values the engine now returns come from lite — pin them."""
    p = lite_chem.compute_properties(ASPIRIN)
    assert p["molecular_weight"] == pytest.approx(180.159, abs=0.01)
    assert p["logp"] == pytest.approx(1.31, abs=0.02)
    assert p["tpsa"] == pytest.approx(63.6, abs=0.1)
    assert p["hbd"] == 1 and p["hba"] == 3
    assert p["lipinski_pass"] is True
