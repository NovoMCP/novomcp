"""
Tree-Guided Agentic Retrieval — MCP Tool Definitions & Execution

Exposes a multi-step narrowing search path that lets the LLM navigate
122M molecules like a chemist working from broad chemical regions down
to specific candidates.

Navigation flow (the LLM calls these in sequence):

  explore_chemical_space  →  drill_into_cluster  →  compare_candidates  →  get_molecule_profile
       (Level 1)                (Level 2+)            (head-to-head)         (single molecule)

Each step narrows the search:
  122M molecules → ~1.2M → ~12K → ~100 → 1

The LLM decides which branch to explore based on cluster summaries
(MW range, avg QED, avg toxicity, scaffold distribution, FAVES status).

Integration:
  - Import TREE_SEARCH_TOOLS and TREE_SEARCH_CREDITS into tools.py
  - Add execution methods to NovoMCPToolExecutor
  - Backend: Cosmos DB 'cluster_hierarchy' container (DiskANN vector index)
"""

import logging
from typing import Dict, List, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)


# =============================================================================
# Tool Credits
# =============================================================================

TREE_SEARCH_CREDITS: Dict[str, int] = {
    "explore_chemical_space": 3,   # Level 1: broad region scan
    "drill_into_cluster": 3,      # Level 2+: zone narrowing
    "compare_candidates": 5,      # Head-to-head with full profiles
    "vector_search": 5,           # Direct DiskANN vector search (replaces old search_similar)
}


# =============================================================================
# Tool Tier (imported from tools.py at integration time)
# =============================================================================

class ToolTier(str, Enum):
    FREE = "free"
    ENTERPRISE = "enterprise"


# =============================================================================
# MCP Tool Definitions
# =============================================================================

TREE_SEARCH_TOOLS = {

    # =========================================================================
    # Step 1: Explore Chemical Space (Level 1 Regions)
    # =========================================================================
    "explore_chemical_space": {
        "name": "explore_chemical_space",
        "title": "Explore Chemical Space",
        "description": (
            "Start a tree-guided search through 122M molecules. Returns the top chemical "
            "regions (Level 1 clusters) that best match your target profile. Each region "
            "contains ~1.2M molecules with summary statistics: MW range, avg QED, avg "
            "toxicity, % orally bioavailable (GI absorption), % BBB-penetrant, % zero-PAINS, "
            "Brenk alert rate, scaffold distribution, and FAVES compliance %. Use this as "
            "the first step — read the summaries, then call drill_into_cluster on the most "
            "promising region to narrow further."
        ),
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Natural language description of what you're looking for. "
                        "Examples: 'kinase inhibitors with low toxicity', "
                        "'small molecules MW 200-400 with high QED', "
                        "'CNS-penetrant compounds with low CYP inhibition'"
                    )
                },
                "smiles": {
                    "type": "string",
                    "description": (
                        "Optional reference SMILES. If provided, finds regions structurally "
                        "similar to this molecule (uses Morgan fingerprint embedding). "
                        "If omitted, uses text query embedding."
                    )
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of regions to return (default 5, max 20)",
                    "default": 5,
                    "maximum": 20
                },
                "constraints": {
                    "type": "object",
                    "description": "Hard filters applied before ranking",
                    "properties": {
                        "mw_min": {"type": "number", "description": "Minimum avg MW for region"},
                        "mw_max": {"type": "number", "description": "Maximum avg MW for region"},
                        "qed_min": {"type": "number", "description": "Minimum avg QED for region"},
                        "clean_pct_min": {"type": "number", "description": "Minimum % FAVES-clean molecules"},
                        "gi_high_pct_min": {"type": "number", "description": "Minimum % orally bioavailable (GI High)"},
                        "bbb_yes_pct_min": {"type": "number", "description": "Minimum % BBB-penetrant"},
                        "pains_clean_pct_min": {"type": "number", "description": "Minimum % zero-PAINS alerts"},
                        "exclude_controlled_heavy": {
                            "type": "boolean",
                            "description": "Exclude regions where >5% are controlled substances",
                            "default": True
                        }
                    }
                }
            },
            "required": []  # Either query or smiles (or both)
        }
    },

    # =========================================================================
    # Step 2: Drill Into Cluster (Level 2+ Zones)
    # =========================================================================
    "drill_into_cluster": {
        "name": "drill_into_cluster",
        "title": "Drill Into Cluster",
        "description": (
            "Explore sub-clusters within a chemical region. Given a cluster ID from "
            "explore_chemical_space (or a previous drill_into_cluster call), returns "
            "its child clusters with detailed summaries. Each child contains ~12K "
            "molecules (Level 2) or ~100 molecules (Level 3). At Level 3, you get "
            "sample molecule CIDs you can pass to get_molecule_profile or compare_candidates."
        ),
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "cluster_id": {
                    "type": "string",
                    "description": "Cluster ID to drill into (e.g., 'L1_C000042' from explore_chemical_space)"
                },
                "query": {
                    "type": "string",
                    "description": "Optional: refine the search within this cluster"
                },
                "smiles": {
                    "type": "string",
                    "description": "Optional: find sub-clusters nearest to this molecule"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of child clusters to return (default 5, max 20)",
                    "default": 5,
                    "maximum": 20
                },
                "sort_by": {
                    "type": "string",
                    "description": "Sort child clusters by this metric",
                    "enum": [
                        "similarity", "qed_mean", "toxicity_min", "molecule_count",
                        "clean_pct", "gi_high_pct", "bbb_yes_pct", "pains_clean_pct"
                    ],
                    "default": "similarity"
                }
            },
            "required": ["cluster_id"]
        }
    },

    # =========================================================================
    # Step 3: Compare Candidates (Head-to-Head)
    # =========================================================================
    "compare_candidates": {
        "name": "compare_candidates",
        "title": "Compare Candidates",
        "description": (
            "Head-to-head comparison of specific molecules. Takes a list of CIDs "
            "(from drill_into_cluster sample_cids or any other source) and returns "
            "their full profiles side-by-side: properties, ADMET predictions, FAVES "
            "compliance, structural alerts. Designed for the final selection step "
            "after narrowing via tree search."
        ),
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "cids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of CIDs to compare (max 20)",
                    "maxItems": 20
                },
                "rank_by": {
                    "type": "string",
                    "description": "Primary ranking criterion for the comparison",
                    "enum": ["qed", "toxicity", "drug_likeness", "synthetic_accessibility", "logp"],
                    "default": "qed"
                },
                "exclude_controlled": {
                    "type": "boolean",
                    "description": "Filter out controlled substances from results",
                    "default": True
                }
            },
            "required": ["cids"]
        }
    },

    # =========================================================================
    # Direct Vector Search (DiskANN — replaces brute-force similarity)
    # =========================================================================
    "vector_search": {
        "name": "vector_search",
        "title": "Vector Similarity Search",
        "description": (
            "Fast approximate nearest-neighbor search over 122M molecules using DiskANN. "
            "Given a query molecule (SMILES), finds the most structurally similar molecules "
            "in <100ms using Morgan fingerprint embeddings. This replaces the old brute-force "
            "Tanimoto search. Use this when you already know what molecule you want analogs "
            "of. For broader exploration, use explore_chemical_space instead."
        ),
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "smiles": {
                    "type": "string",
                    "description": "Query SMILES string"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of similar molecules to return (max 100)",
                    "default": 10,
                    "maximum": 100
                },
                "min_similarity": {
                    "type": "number",
                    "description": "Minimum cosine similarity threshold (0-1)",
                    "default": 0.7
                },
                "property_filters": {
                    "type": "object",
                    "description": "Optional property filters applied post-search",
                    "properties": {
                        "mw_min": {"type": "number"},
                        "mw_max": {"type": "number"},
                        "qed_min": {"type": "number"},
                        "toxicity_max": {"type": "number"},
                        "exclude_controlled": {"type": "boolean", "default": True},
                        "exclude_pains": {"type": "boolean", "default": False}
                    }
                }
            },
            "required": ["smiles"]
        }
    },
}


# =============================================================================
# Execution Methods (to be integrated into NovoMCPToolExecutor)
# =============================================================================

class TreeSearchExecutor:
    """Execution logic for tree-guided retrieval tools.

    In production, integrate these methods into NovoMCPToolExecutor in tools.py.
    Each method calls the faves-compliance service which fronts Cosmos DB.
    """

    def __init__(self, call_service_fn, lookup_enriched_fn, map_cosmos_fn):
        """
        Args:
            call_service_fn: async fn(service, path, data, timeout) — from NovoMCPToolExecutor
            lookup_enriched_fn: async fn(smiles) — _lookup_enriched from tools.py
            map_cosmos_fn: fn(raw) — _map_cosmos_to_mcp from tools.py
        """
        self._call_service = call_service_fn
        self._lookup_enriched = lookup_enriched_fn
        self._map_cosmos = map_cosmos_fn

    async def execute_explore_chemical_space(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Step 1: Find the best Level 1 chemical regions.

        Two query modes:
          - SMILES provided: generate Morgan fingerprint embedding, vector search L1 centroids
          - Text query only: generate text embedding via Azure OpenAI, vector search L1 centroids

        Returns cluster summaries the LLM reads to decide which branch to explore.
        """
        query = args.get("query", "")
        smiles = args.get("smiles")
        top_k = min(args.get("top_k", 5), 20)
        constraints = args.get("constraints", {})

        try:
            response = await self._call_service(
                "faves-compliance",
                "/api/tree/explore",
                {
                    "query": query,
                    "smiles": smiles,
                    "top_k": top_k,
                    "constraints": constraints,
                },
                timeout=30.0
            )

            if response.status_code == 200:
                data = response.json()
                regions = data.get("regions", [])

                return {
                    "success": True,
                    "data": {
                        "level": 1,
                        "total_regions": data.get("total_regions", len(regions)),
                        "query": query,
                        "regions": regions,
                        "navigation_hint": (
                            "Choose the most relevant region and call drill_into_cluster "
                            "with its cluster_id to see sub-clusters."
                        )
                    }
                }

            return {"success": False, "error": f"Explore failed: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": f"Chemical space exploration failed: {str(e)}"}

    async def execute_drill_into_cluster(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Step 2: Drill into a cluster to see its children.

        Reads the cluster document, fetches its children from the hierarchy,
        optionally re-ranks by a query vector.

        At Level 3 (leaf clusters), returns sample_cids for direct molecule lookup.
        """
        cluster_id = args.get("cluster_id")
        if not cluster_id:
            return {"success": False, "error": "Missing required parameter: cluster_id"}

        query = args.get("query", "")
        smiles = args.get("smiles")
        top_k = min(args.get("top_k", 5), 20)
        sort_by = args.get("sort_by", "similarity")

        try:
            response = await self._call_service(
                "faves-compliance",
                "/api/tree/drill",
                {
                    "cluster_id": cluster_id,
                    "query": query,
                    "smiles": smiles,
                    "top_k": top_k,
                    "sort_by": sort_by,
                },
                timeout=30.0
            )

            if response.status_code == 200:
                data = response.json()
                children = data.get("children", [])
                current_level = data.get("current_level", 1)
                child_level = current_level + 1

                result = {
                    "success": True,
                    "data": {
                        "parent_cluster": cluster_id,
                        "parent_description": data.get("parent_description", ""),
                        "child_level": child_level,
                        "total_children": data.get("total_children", len(children)),
                        "children": children,
                    }
                }

                # At leaf level, provide direct molecule access hint
                if child_level >= 3:
                    result["data"]["navigation_hint"] = (
                        "These are leaf clusters with sample molecule CIDs. "
                        "Call compare_candidates with the sample_cids to see "
                        "full profiles, or call get_molecule_profile for a single molecule."
                    )
                else:
                    result["data"]["navigation_hint"] = (
                        "Call drill_into_cluster again with a child's cluster_id "
                        "to narrow further."
                    )

                return result

            return {"success": False, "error": f"Drill failed: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": f"Cluster drill failed: {str(e)}"}

    async def execute_compare_candidates(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Step 3: Head-to-head comparison of specific molecules.

        Fetches full profiles for each CID from the enriched database,
        ranks them by the specified criterion, and returns a comparison table.
        """
        cids = args.get("cids", [])
        if not cids:
            return {"success": False, "error": "Missing required parameter: cids"}
        if len(cids) > 20:
            return {"success": False, "error": "Maximum 20 CIDs per comparison"}

        rank_by = args.get("rank_by", "qed")
        exclude_controlled = args.get("exclude_controlled", True)

        try:
            response = await self._call_service(
                "faves-compliance",
                "/api/tree/compare",
                {
                    "cids": cids,
                    "rank_by": rank_by,
                    "exclude_controlled": exclude_controlled,
                },
                timeout=30.0
            )

            if response.status_code == 200:
                data = response.json()
                candidates = data.get("candidates", [])

                return {
                    "success": True,
                    "data": {
                        "total_compared": len(candidates),
                        "ranked_by": rank_by,
                        "candidates": candidates,
                        "navigation_hint": (
                            "Use get_molecule_profile for detailed 3D visualization "
                            "of any candidate, or optimize_molecule to generate variants."
                        )
                    }
                }

            return {"success": False, "error": f"Compare failed: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": f"Candidate comparison failed: {str(e)}"}

    async def execute_vector_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Direct DiskANN vector search — fast ANN over 122M molecules.

        Generates Morgan fingerprint embedding for the query SMILES,
        runs VectorDistance() search on Cosmos DB with DiskANN index.
        Sub-100ms latency at 95%+ recall.
        """
        smiles = args.get("smiles")
        if not smiles:
            return {"success": False, "error": "Missing required parameter: smiles"}

        top_k = min(args.get("top_k", 10), 100)
        min_similarity = args.get("min_similarity", 0.7)
        property_filters = args.get("property_filters", {})

        try:
            response = await self._call_service(
                "faves-compliance",
                "/api/tree/vector-search",
                {
                    "smiles": smiles,
                    "top_k": top_k,
                    "min_similarity": min_similarity,
                    "property_filters": property_filters,
                },
                timeout=30.0
            )

            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])

                return {
                    "success": True,
                    "data": {
                        "query_smiles": smiles,
                        "total_results": len(results),
                        "search_time_ms": data.get("search_time_ms"),
                        "results": results,
                    }
                }

            return {"success": False, "error": f"Vector search failed: {response.status_code}"}

        except Exception as e:
            return {"success": False, "error": f"Vector search failed: {str(e)}"}
