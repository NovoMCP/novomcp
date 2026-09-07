#!/usr/bin/env python3
"""
Two MCP servers, one workflow: AWS Open Data discovery -> NovoMCP analysis.

An agent asks AWS's Registry of Open Data (over MCP) for cheminformatics data,
gets exactly one dataset back -- the NovoMCP Open Corpus -- then takes a compound
that corpus contains and analyzes it through the NovoMCP engine (over MCP).

Two independent MCP servers compose into one pipeline:
  1. awslabs.roda-mcp-server  (stdio, launched via uvx)      -- discovery
  2. NovoMCP engine           (streamable-http on :8018)      -- analysis

Prerequisites:
  - uv installed (https://astral.sh/uv). Run this file with:
        uv run --with mcp python two_mcp_demo.py
  - The NovoMCP engine reachable at NOVOMCP_URL (default http://localhost:8018/).
    Start it in another terminal with:  uvx novomcp
    (or: docker run --rm -p 8018:8018 ghcr.io/novomcp/novomcp:latest)

Nothing here needs an AWS account or an API key. The registry index and the
corpus both read anonymously.
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

NOVOMCP_URL = os.environ.get("NOVOMCP_URL", "http://localhost:8018/")

# A compound the corpus contains. The NovoMCP Open Corpus is all 122,454,458
# PubChem compounds, so any PubChem CID is in it. Imatinib (Gleevec), CID 5291,
# a landmark targeted cancer therapy -- a fitting molecule to pull from a
# drug-discovery corpus.
DEMO_CID = 5291
DEMO_NAME = "imatinib (Gleevec)"
DEMO_SMILES = "CC1=C(C=C(C=C1)NC(=O)C2=CC=C(C=C2)CN3CCN(CC3)C)NC4=NC=CC(=N4)C5=CN=CC=C5"


def rule(title):
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def first_text(result):
    for c in result.content:
        t = getattr(c, "text", None)
        if t:
            return t
    return str(result.content)


async def discover(query):
    """Ask AWS's RODA MCP server for datasets matching a plain-language query."""
    params = StdioServerParameters(command="uvx", args=["awslabs.roda-mcp-server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool("search_datasets", {"query": query})
            return json.loads(first_text(res))


async def analyze(smiles):
    """Profile a molecule through the NovoMCP engine over streamable-http MCP."""
    async with streamable_http_client(NOVOMCP_URL) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            profile = await session.call_tool("get_molecule_profile", {"smiles": smiles})
            return json.loads(first_text(profile))


async def main():
    rule("STEP 1  discovery via AWS Registry of Open Data (roda-mcp-server, MCP)")
    print("  query: 'cheminformatics'  (plain language, not a keyword filter)")
    hits = await discover("cheminformatics")
    print(f"  total_count: {hits['total_count']}")
    for r in hits["results"]:
        print(f"    -> {r['name']}  [{r['slug']}]")
        print(f"       license: {r['license']}")
    if hits["total_count"] != 1:
        print("  NOTE: expected exactly 1 result. The registry is live; a new")
        print("  chemistry dataset may have landed. The claim is time-stamped.")

    rule("STEP 2  the same finding for 'ADMET'")
    admet = await discover("ADMET")
    print(f"  query 'ADMET' -> total_count: {admet['total_count']}")
    for r in admet["results"]:
        print(f"    -> {r['name']}")

    corpus = hits["results"][0]
    rule("STEP 3  bridge: pull a compound the discovered corpus contains")
    print(f"  the corpus is all 122,454,458 PubChem compounds (managed_by "
          f"{corpus['managed_by']}).")
    print(f"  taking one of them: {DEMO_NAME}, PubChem CID {DEMO_CID}")
    print(f"  SMILES: {DEMO_SMILES}")

    rule("STEP 4  analysis via the NovoMCP engine (streamable-http, MCP)")
    prof = await analyze(DEMO_SMILES)
    props = prof.get("properties", prof)
    print("  get_molecule_profile -> RDKit descriptors, computed in-process:")
    for k in ("molecular_weight", "logp", "tpsa", "hbd", "hba",
              "rotatable_bonds", "aromatic_rings"):
        if k in props:
            print(f"    {k:18} {props[k]}")
    if not prof.get("admet_available"):
        print("  admet: not wired on this install. get_molecule_profile also")
        print("  returns 30+ ADMET predictions once you point ADDIE_MODELS_URL at")
        print("  the open addie-models service. Capability-gated, not edition-gated.")

    rule("DONE")
    print("  Two MCP servers, one pipeline: AWS RODA discovered the corpus in")
    print("  plain language (sole cheminformatics/ADMET result), and the NovoMCP")
    print("  engine analyzed a compound from it. No AWS account, no API key.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
