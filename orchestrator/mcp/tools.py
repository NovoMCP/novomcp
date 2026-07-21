"""
NovoMCP Tool Definitions

Data Flow:
1. Known molecules (present in the enriched index) → Return pre-computed ADMET + FAVES
2. Novel molecules (not in DB) → Run FAVES context-free check on-the-fly
3. Context-dependent queries → Runtime evaluation with user-provided context
4. 3D properties → On-demand computation via NovoMD
5. Literature/Patents → Pinecone vector search

Services:
- enriched-search: Query the enriched molecule index (parquet) — returns pre-computed data
- faves-compliance: On-the-fly FAVES for novel molecules + context-dependent compliance
- molmim-optimizer: Molecular optimization (auto-runs FAVES on output)
- openfold3: Protein structure prediction
- novomd: 3D molecular properties (geometry, energy, electrostatics, surface/volume, coordinates)
- chem-props: RDKit property calculations (Lipinski, QED, SA_Score, etc.)
- addie-models: 31 ML models for ADMET predictions
- pinecone: Literature and patent vector search
"""

import asyncio
import hashlib
import logging
import os
import json
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
import httpx
import redis.asyncio as aioredis
from mcp.tree_search_tools import TREE_SEARCH_TOOLS, TREE_SEARCH_CREDITS, TreeSearchExecutor
from version import __version__ as ENGINE_VERSION

logger = logging.getLogger(__name__)

# Ertl–Schuffenhauer synthetic accessibility (1=easy … 10=hard) via RDKit's
# contrib sascorer. The chem-props/FAVES services return an unreliable SA
# (observed flat 1.0 — aspirin came back 1.0 vs the real ~1.58), so we compute
# it locally wherever we surface sa_score. Lazy import; None on any failure.
_SASCORER = None


def _compute_sa_score(smiles):
    global _SASCORER
    try:
        from rdkit import Chem
        if _SASCORER is None:
            import os as _os, sys as _sys
            from rdkit.Chem import RDConfig
            _sa_dir = _os.path.join(RDConfig.RDContribDir, "SA_Score")
            if _sa_dir not in _sys.path:
                _sys.path.append(_sa_dir)
            import sascorer as _sc
            _SASCORER = _sc
        mol = Chem.MolFromSmiles(smiles or "")
        if mol is None:
            return None
        return round(_SASCORER.calculateScore(mol), 2)
    except Exception:
        return None


class ToolTier(str, Enum):
    """Tool access tiers for rate limiting and pricing."""
    FREE = "free"
    CORE = "core"
    PRO = "pro"        # Legacy — mapped to FREE
    TEAM = "team"
    ENTERPRISE = "enterprise"


def normalize_tier(tier: str) -> str:
    """Map legacy tiers to current tiers.

    Current pricing: Free Trial + Core (PAYG) + Team ($500/mo) + Enterprise.
    Legacy pro users are grandfathered as free.
    """
    if tier == "pro":
        return "free"
    return tier


# =============================================================================
# TOOL CREDIT COSTS (Hybrid Billing v3.0)
# =============================================================================
# Funnel auto-logging: tools that should NOT fire an exploration event.
# - funnel/audit tools themselves (recursion, noise)
# - high-frequency polling and metadata reads (would flood the trail)
FUNNEL_AUTOLOG_SKIP = {
    "save_funnel_stage",
    "save_funnel_context",
    "save_funnel_memory",     # recursion guard — writes to SQL directly, not via auto-log
    "get_funnel_audit",
    "get_funnel_context",
    "get_pipeline_audit",
    "search_prior_runs",      # audit read with lazy backstop — own write path
    "run_novo_ag",            # meta trigger — returns prompt text, not a discovery event
    "get_credit_usage",
    "get_platform_info",
    "novo_compute_info",
    "get_structure_result",
    "get_file_status",
    "list_files",
}


def _redact_arguments(args: Dict[str, Any]) -> Dict[str, Any]:
    """Shrink tool arguments for the audit log: truncate large strings and long lists."""
    out: Dict[str, Any] = {}
    for k, v in (args or {}).items():
        if isinstance(v, str):
            out[k] = v if len(v) <= 500 else f"{v[:500]}...<truncated {len(v) - 500} chars>"
        elif isinstance(v, list):
            if len(v) <= 20:
                out[k] = v
            else:
                out[k] = {"_list_summary": True, "length": len(v), "sample": v[:5]}
        elif isinstance(v, dict):
            out[k] = {"_dict_summary": True, "keys": list(v.keys())[:20], "key_count": len(v)}
        else:
            out[k] = v
    return out


# Semaphore to cap concurrent autolog HTTP calls. Dashboard-aggregator uses a
# single shared pymssql connection; N=3 prevents auth-query starvation under burst.
_AUTOLOG_SEMAPHORE = asyncio.Semaphore(3)


def _sanitize_for_json(obj: Any) -> Any:
    """Replace float NaN/Inf with None so json.dumps never silently drops the payload."""
    if isinstance(obj, float):
        if obj != obj or obj == float("inf") or obj == float("-inf"):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return obj


# Base-ADDIE heads that return constant, non-discriminating values (~0.50 regardless
# of structure) — suppressed rather than surface a flat constant an agent would read
# as a real prediction. Re-enable a head here once it's retrained/replaced.
#
# UN-SUPPRESSED 2026-06-23 (WS4): nuclear_receptors + stress_response were retrained
# as TDC SOTA endpoints (the full Tox21 panel, AUROC 0.77–0.94) and now discriminate,
# so they're removed from the category suppression. The remaining toxicity keys below
# are still the unvalidated base-ADDIE heads (NOT retrained) and stay suppressed.
_SUPPRESSED_ADMET_CATEGORIES = ()
_SUPPRESSED_TOXICITY_KEYS = (
    # clinical_toxicity un-suppressed 2026-06-23: retrained as the ClinTox TDC
    # endpoint (AUROC 0.910), now discriminating.
    # Cardiotox now covered by hERG + the validated DICTrank head `cardiotoxicity_dict`.
    # The legacy base-ADDIE cardiotox heads (1d/5d/10d/30d/max) are all suppressed
    # 2026-06-24: superseded by cardiotoxicity_dict, and cardiotoxicity_max sat at a
    # ~0.50 floor on benigns (sucrose, acetaminophen) — an agent composing a "max
    # severity" score must not pick it over cardiotoxicity_dict. developmental +
    # reproductive remain unvalidated (would need curated DART/ToxRefDB data).
    "cardiotoxicity_1d", "cardiotoxicity_5d", "cardiotoxicity_10d",
    "cardiotoxicity_30d", "cardiotoxicity_max",
    "developmental_toxicity", "reproductive_toxicity",
)


def _prune_unvalidated_admet(block: Dict[str, Any]) -> Dict[str, Any]:
    """Strip the non-discriminating base-ADDIE heads from an ADMET block in
    place. Works on both the known-molecule mapper output (categories nested in
    `admet`) and the novel-molecule predict_admet result (categories at top
    level), since both share the same key layout."""
    for cat in _SUPPRESSED_ADMET_CATEGORIES:
        block.pop(cat, None)
    tox = block.get("toxicity")
    if isinstance(tox, dict):
        for key in _SUPPRESSED_TOXICITY_KEYS:
            tox.pop(key, None)
        if not tox:
            block.pop("toxicity", None)
    return block


# --- Phase-1 real-time bridge -------------------------------------------------
# These heads were retrained (live addie is correct) but the 122M pre-computed
# corpus is still stale (~0.50 / absent) until the batch re-compute lands. So for
# KNOWN molecules we strip them from the corpus admet and overlay LIVE addie values.
# Remove a field from these tuples once Phase 3/4 recomputes its corpus column
# (see docs/admet-retrain-122m-rollout-runbook.md).
_BRIDGE_LIVE_CATEGORIES = ("nuclear_receptors", "stress_response")
# hepatotoxicity added 2026-06-23: the DILI head was retrained (FDA gold standard) to fix
# a clinical inversion; the 122M corpus still holds the OLD inverted values, so known
# molecules must serve the new DILI live until Phase 3 recomputes it.
_BRIDGE_LIVE_TOXICITY_KEYS = ("clinical_toxicity", "cardiotoxicity_dict", "hepatotoxicity")


def _strip_bridge_from_corpus(admet: Dict[str, Any]) -> Dict[str, Any]:
    """Remove the bridge heads from a corpus-derived admet so we never surface stale
    values for known molecules (they get overlaid live)."""
    if not isinstance(admet, dict):
        return admet
    for cat in _BRIDGE_LIVE_CATEGORIES:
        admet.pop(cat, None)
    tox = admet.get("toxicity")
    if isinstance(tox, dict):
        for k in _BRIDGE_LIVE_TOXICITY_KEYS:
            tox.pop(k, None)
    return admet


def _overlay_bridge_live(admet: Dict[str, Any], live: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay the bridge heads from a live addie result (`_build_admet_result` shape)
    onto a corpus admet block, in place."""
    if not isinstance(admet, dict) or not isinstance(live, dict):
        return admet
    for cat in _BRIDGE_LIVE_CATEGORIES:
        if live.get(cat):
            admet[cat] = live[cat]
    live_tox = live.get("toxicity") or {}
    if any(k in live_tox for k in _BRIDGE_LIVE_TOXICITY_KEYS):
        tox = admet.setdefault("toxicity", {})
        for k in _BRIDGE_LIVE_TOXICITY_KEYS:
            if k in live_tox:
                tox[k] = live_tox[k]
    return admet


def _build_admet_result(smiles: str, predictions: Dict[str, Any], mol_error: Optional[str] = None) -> Dict[str, Any]:
    """Map a flat addie-models predictions dict into the structured ADMET result
    (metabolism / toxicity / nuclear_receptors / stress_response / properties),
    strip nulls + empty categories, suppress the non-discriminating heads, and
    surface unavailable endpoints. Shared by the single-molecule predict_admet path
    and the batched novel-molecule path (_predict_admet_batch_addie) so both emit an
    identical shape."""
    result = {
        "smiles": smiles,
        "source": "addie-models",
        # Full SOTA panel — keys mirror _map_cosmos_to_mcp (known molecules) so
        # novel + known ADMET responses are symmetric. addie computes all of these;
        # before this they were silently dropped for novel molecules, hiding 2 of
        # the 5 SOTA wins (CYP3A4 substrate, clearance hepatocyte) plus lipophilicity
        # and LD50. Read addie_field prediction keys (note capital-L variants).
        "absorption": {
            "caco2_permeability": predictions.get("caco2_permeability"),
            "hia_probability": predictions.get("hia_probability"),
            "bioavailability": predictions.get("bioavailability_probability"),
            "pgp_inhibitor": predictions.get("pgp_inhibitor_probability"),
            "pgp_substrate": predictions.get("pgp_substrate_probability"),
            "lipophilicity_log_ratio": predictions.get("lipophilicity_log_ratio"),
            "aqueous_solubility_log_mol_l": predictions.get("aqueous_solubility_log_mol_L"),
        },
        "distribution": {
            "bbb_penetration": predictions.get("bbb_penetration_probability"),
            "ppbr_percent": predictions.get("ppbr_percent"),
            "vdss_l_kg": predictions.get("vdss_L_kg"),
        },
        "metabolism": {
            "cyp1a2_inhibitor": predictions.get("cyp1a2_inhibitor_probability"),
            "cyp2c9_inhibitor": predictions.get("cyp2c9_inhibitor_probability"),
            "cyp2c19_inhibitor": predictions.get("cyp2c19_inhibitor_probability"),
            "cyp2d6_inhibitor": predictions.get("cyp2d6_inhibitor_probability"),
            "cyp3a4_inhibitor": predictions.get("cyp3a4_inhibitor_probability"),
            "cyp2c9_substrate": predictions.get("cyp2c9_substrate_probability"),
            "cyp2d6_substrate": predictions.get("cyp2d6_substrate_probability"),
            "cyp3a4_substrate": predictions.get("cyp3a4_substrate_probability"),
            "cyp_inhibition_risk_score": predictions.get("cyp_inhibition_risk_score"),
            "cyp_substrate_max_probability": predictions.get("cyp_substrate_max_probability"),
        },
        "excretion": {
            "half_life_hr": predictions.get("half_life_hr"),
            "clearance_hepatocyte": predictions.get("clearance_hepatocyte"),
            "clearance_microsome": predictions.get("clearance_microsome"),
        },
        "toxicity": {
            "hepatotoxicity": predictions.get("hepatotoxicity_probability"),
            "cardiotoxicity_1d": predictions.get("cardiotoxicity_1d_probability"),
            "cardiotoxicity_5d": predictions.get("cardiotoxicity_5d_probability"),
            "cardiotoxicity_10d": predictions.get("cardiotoxicity_10d_probability"),
            "cardiotoxicity_30d": predictions.get("cardiotoxicity_30d_probability"),
            "cardiotoxicity_max": predictions.get("cardiotoxicity_max_probability"),
            "ames_mutagenicity": predictions.get("ames_mutagenicity_probability"),
            "carcinogenicity": predictions.get("carcinogenicity_probability"),
            "clinical_toxicity": predictions.get("clinical_toxicity_probability"),
            "developmental_toxicity": predictions.get("developmental_toxicity_probability"),
            "reproductive_toxicity": predictions.get("reproductive_toxicity_probability"),
            "respiratory_toxicity": predictions.get("respiratory_toxicity_probability"),
            "eye_corrosion": predictions.get("eye_corrosion_probability"),
            "eye_irritation": predictions.get("eye_irritation_probability"),
            "herg_inhibition": predictions.get("herg_blocker_probability"),
            "cardiotoxicity_dict": predictions.get("cardiotoxicity_dict_probability"),
            "ld50_log_mol_kg": predictions.get("ld50_log_mol_kg"),
            "overall_toxicity_score": predictions.get("overall_toxicity_score"),
        },
        "nuclear_receptors": {
            "ahr_agonist": predictions.get("nr_ahr_agonist_probability"),
            "ar_lbd_agonist": predictions.get("nr_ar_lbd_agonist_probability"),
            "ar_agonist": predictions.get("nr_ar_agonist_probability"),
            "aromatase_inhibitor": predictions.get("nr_aromatase_inhibitor_probability"),
            "er_lbd_agonist": predictions.get("nr_er_lbd_agonist_probability"),
            "er_agonist": predictions.get("nr_er_agonist_probability"),
            "ppar_gamma_agonist": predictions.get("nr_ppar_gamma_agonist_probability"),
        },
        "stress_response": {
            "are_activation": predictions.get("sr_are_activation_probability"),
            "atad5_activation": predictions.get("sr_atad5_activation_probability"),
            "hse_activation": predictions.get("sr_hse_activation_probability"),
            "mmp_activation": predictions.get("sr_mmp_activation_probability"),
            "p53_activation": predictions.get("sr_p53_activation_probability"),
        },
        "properties": {
            "molecular_weight": predictions.get("molecular_weight"),
            "logp": predictions.get("logp"),
            "tpsa": predictions.get("tpsa"),
            "hba": predictions.get("hba"),
            "hbd": predictions.get("hbd"),
            "rotatable_bonds": predictions.get("rotatable_bonds"),
        },
        "raw_predictions": predictions,
    }

    unavailable = [
        f"{cat}.{k}"
        for cat in ("metabolism", "toxicity", "nuclear_receptors", "stress_response")
        for k, v in result[cat].items()
        if v is None
    ]

    for category in ["absorption", "distribution", "metabolism", "excretion", "toxicity", "nuclear_receptors", "stress_response", "properties"]:
        result[category] = {k: v for k, v in result[category].items() if v is not None}

    result = {k: v for k, v in result.items() if v or k in ["smiles", "source"]}
    _prune_unvalidated_admet(result)

    if unavailable or mol_error:
        result["predictions_unavailable"] = {
            "models": unavailable,
            "reason": mol_error or "addie-models returned no value for these endpoints (see service logs)",
        }
    return result


def _summarize_result_data(data: Any) -> Dict[str, Any]:
    """Extract a small fingerprint of a tool result for the audit log."""
    if data is None:
        return {}
    if not isinstance(data, dict):
        return {"_type": type(data).__name__}
    summary: Dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, (int, float, bool)) or v is None:
            summary[k] = v
        elif isinstance(v, str):
            summary[k] = v if len(v) <= 200 else f"{v[:200]}...<truncated>"
        elif isinstance(v, list):
            summary[k] = {"_list": True, "length": len(v)}
        elif isinstance(v, dict):
            summary[k] = {"_dict": True, "keys": list(v.keys())[:10]}
        else:
            summary[k] = type(v).__name__
    return summary


# =============================================================================
# 1 Credit = $1.00 USD
# Tiers include monthly credits; overage billed at tier-specific rate
# Costs are based on computational complexity and external API usage

TOOL_CREDITS: Dict[str, int] = {
    # Tier 1: Free/Basic Tools (0-2 credits)
    "get_molecule_info": 1,           # Simple RDKit computation
    "get_platform_info": 0,           # Free - encourages visibility
    "get_credit_usage": 0,            # Free - users should always see their credits
    "get_structure_result": 0,        # Free - result retrieval
    "get_job_status": 0,              # Free - status checks
    "cancel_job": 0,                   # Free - aborting a job is always free
    "list_jobs": 0,                    # Free - job listing
    "get_pipeline_audit": 0,           # Free - audit trail retrieval
    "save_funnel_stage": 0,            # Free - log a funnel stage for reproducibility
    "get_funnel_audit": 0,             # Free - retrieve full funnel audit log
    "list_funnels": 0,                 # Free - list recent discovery funnels with metadata
    "save_funnel_memory": 0,           # Free - write terminal summary for cross-run learning
    "search_prior_runs": 0,            # Free - query cross-run memory (includes lazy backstop write)
    "run_novo_ag": 0,                  # Free - trigger that returns the autonomous prompt text
    "audit_system": 0,                 # Free - pre-flight MD target classification (no GPU)
    "parameterize_metal": 50,          # MCPB.py QM→FF bridge (CPU-heavy, no GPU)
    "generate_upload_url": 0,          # Free - file upload is free, consuming tools charge
    "get_file_status": 0,              # Free - file status check
    "list_files": 0,                   # Free - file inventory

    # Tier 2: Enriched Lookup (2-5 credits)
    "get_molecule_profile": 2,        # Database lookup + FAVES check
    "get_protein_structure": 5,       # PDB fetch (free) or OpenFold3 fallback (+100)
    "check_compliance": 3,            # FAVES context evaluation

    # Tier 3: Search/Filter (5-10 credits)
    "search_similar": 5,              # Vector similarity search
    "filter_molecules": 5,            # Database query with filters
    "search_literature": 5,           # Pinecone vector search
    "search_patents": 5,              # Pinecone vector search
    "search_biorxiv": 3,              # bioRxiv preprint search (free API)
    "search_chembl": 5,               # ChEMBL compound/target search
    "search_clinical_trials": 3,      # ClinicalTrials.gov search (free API)
    "validate_target": 8,             # Adversarial validation (orchestrates 5-6 internal calls, lowered April 4)
    "batch_profile": 10,              # Batch processing (per batch)

    # Tier 4: ML Inference (10-25 credits)
    "calculate_properties": 10,       # RDKit + fingerprints
    "get_3d_properties": 15,          # NovoMD conformer generation
    "predict_admet": 20,              # 31 ML model inference
    "predict_clinical_outcomes": 25,  # Orchestrates chem-props + FAVES + addie-models → novoexpert
    "optimize_molecule": 25,          # MolMIM API + FAVES

    # Tier 5: Heavy Compute (50-100 credits)
    "screen_library": 50,             # Batch screening
    "predict_structure": 100,         # OpenFold3 GPU inference

    # Tier 6: Quantum/Long-running (v4/v5 - 150-500 credits)
    "lead_optimization": 150,         # Multi-objective optimization
    "dock_molecules": 10,             # AutoDock-GPU base cost (dynamic: 10 + 5/molecule)
    "run_molecular_dynamics": 250,    # GROMACS simulation
    "generate_dynamics": 25,          # GPU-tier: A100 for 1-5 minutes

    # Tier 7: Property Prediction (pKa, solubility, BDE)
    "predict_pka": 10,                # pKa prediction (ionizable groups)
    "predict_solubility": 10,         # Aqueous solubility (LogS)
    "predict_bde": 15,                # Bond dissociation energy (alfabet)

    # Tier 7b: QM Compute
    "run_qm_calculation": 20,         # xTB energy/optimize/solvation
    "run_qm_hessian": 30,             # xTB frequency/thermochemistry (--hess)
    "predict_frontier_orbitals": 20,  # HOMO/LUMO/emission/OLED classification (now with stTDA)
    "run_excited_states": 25,         # sTDA-xTB excited states (S1/T1/oscillator strengths)
    "predict_redox_potential": 50,    # Oxidation/reduction potential (3 xTB optimizations)
    "predict_reaction_thermodynamics": 60,  # ΔG/ΔH/TΔS for reactions (Hessian per species)
    "find_transition_state": 80,      # NEB transition state search (activation barrier)
    "run_conformer_search": 25,       # CREST or RDKit conformer ensemble
    "dock_with_strain": 15,           # xTB strain energy on docked pose

    # Tier 7c: Materials Database
    "search_materials_project": 5,    # Materials Project lookup (formula, band gap, stability)

    # Tier 7d: Neural Network Potentials
    "compute_energy": 5,              # NNP energy + forces (ANI-2x/MACE, fast)
    "optimize_geometry_nnp": 10,      # NNP geometry optimization (ASE BFGS + MACE/ANI-2x)

    # Tier 8: Omics Tools
    "target_discovery": 10,           # Cosmos omics_targets query
    "stratify_patients": 15,          # Cosmos omics_pgx + omics_resistance query

    # Tier 9: Funnel Persistence (free)
    "save_funnel_context": 0,         # Free — persistence utility
    "get_funnel_context": 0,          # Free — retrieval utility

    # Tier 7: Enterprise Data Export/Import
    "push_to_destination": 5,                 # Export to connected destinations
    "pull_from_source": 5,                    # Base cost; pipeline costs computed dynamically
}

# Merge tree-guided retrieval tool credits
TOOL_CREDITS.update(TREE_SEARCH_CREDITS)

# Batch-capable tools. Maps tool_name → argument name that holds the batch list.
# Pre-flight credit check multiplies TOOL_CREDITS[tool] by len(args[batch_param])
# to prevent partial-batch overshoot (e.g. 7 of 10 docked, 3 failed, credits drained).
def _kruskal_mst(n_nodes: int, edges: list) -> list:
    """Minimum spanning tree via Kruskal's algorithm.

    Args:
        n_nodes: number of nodes
        edges: [(i, j, weight), ...] — lower weight = preferred edge

    Returns list of MST edges: [(i, j, weight), ...]
    """
    parent = list(range(n_nodes))
    rank = [0] * n_nodes

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra == rb:
            return False
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1
        return True

    mst = []
    for i, j, w in sorted(edges, key=lambda e: e[2]):
        if union(i, j):
            mst.append((i, j, w))
        if len(mst) == n_nodes - 1:
            break
    return mst


BATCH_TOOLS: Dict[str, str] = {
    "batch_profile": "smiles_list",
    "screen_library": "smiles_list",
    "dock_molecules": "smiles_list",
}

# Pull row limits by tier (data connectors require Team+)
PULL_ROW_LIMITS = {
    "free": 0,
    "core": 0,
    "team": 10000,
    "enterprise": 10000,
}

# =============================================================================
# TIER BILLING CONFIGURATION (v6.0 — Free Trial + Core + Scale + Enterprise)
# =============================================================================
# Display-only mirror of the canonical table in
# NovoServices/dashboard-aggregator/routers/mcp.py:95. dashboard-aggregator
# is the source of truth — it drives signup grants, tier changes, monthly
# resets, and deductions via sp_record_tool_usage. This local copy is used
# only by get_credit_usage (below) to render the user-facing dashboard.
# Keep this table SYNCED with dashboard-aggregator's TIER_BILLING. ISSUES.md
# B8 (resolved 2026-05-13) was the bug where these had drifted apart.
#
# Credits never expire for Core packs. Scale/Enterprise renew monthly.

TIER_BILLING: Dict[str, Dict[str, Any]] = {
    "free": {
        "credits_included": 750,
        "overage_rate": 0.0,           # No overage — trial ends at 0
        "overage_allowed": False,
        "monthly_price": 0,
    },
    "core": {
        "credits_included": 0,         # Pay-as-you-go, no monthly included
        "overage_rate": 0.0,
        "overage_allowed": False,      # Must purchase credit packs
        "monthly_price": 0,
    },
    "team": {
        "credits_included": 15000,
        "overage_rate": 0.33,
        "overage_allowed": True,
        "monthly_price": 500,
    },
    "enterprise": {
        "credits_included": 50000,     # Custom, use as default
        "overage_rate": 0.33,          # Negotiated, use as default
        "overage_allowed": True,
        "monthly_price": 0,            # Custom pricing
    },
}


# =============================================================================
# MCP TOOL DEFINITIONS
# =============================================================================

MCP_TOOLS = {
    # =========================================================================
    # Molecular Intelligence
    # =========================================================================
    "get_molecule_profile": {
        "name": "get_molecule_profile",
        "title": "Get Molecule Profile",
        "description": "Full molecular profile — the PRIMARY tool for profiling any molecule. Always returns live RDKit physicochemical properties (MW, logP, TPSA, HBD/HBA, rotatable bonds, aromatic rings, QED, Lipinski pass) and RDKit-based structural alerts (PAINS, Brenk). ADMET predictions and regulatory compliance are attached when the corresponding optional services are configured; otherwise those blocks come back null with an availability flag — never an error.",
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
                    "description": "SMILES string representing the molecular structure"
                }
            },
            "required": ["smiles"]
        }
    },

    "get_molecule_info": {
        "name": "get_molecule_info",
        "title": "Get Molecule Info",
        "description": "Quick lookup of basic molecular properties only (MW, formula, LogP, TPSA, H-bond counts). Lightweight — no ADMET or compliance data. Use get_molecule_profile instead if you need a complete analysis.",
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
                    "description": "SMILES string representing the molecular structure"
                }
            },
            "required": ["smiles"]
        }
    },

    "get_protein_structure": {
        "name": "get_protein_structure",
        "title": "Get Protein Structure",
        "description": "Smart protein structure resolver with interactive 3D visualization. Accepts: (1) PDB ID (e.g., '1M17'), (2) Protein name (e.g., 'EGFR', 'CDK2'), or (3) Amino acid sequence. First tries to fetch from RCSB PDB (validated experimental structures). If not found and sequence provided, falls back to OpenFold3 prediction. Supports common drug targets: EGFR, CDK2, BRAF, ALK, JAK2, BTK, ABL, HIV Protease, SARS-CoV-2 Mpro, etc.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Protein identifier: PDB ID (e.g., '1M17'), protein name (e.g., 'EGFR'), or UniProt ID (e.g., 'P00533')"
                },
                "sequence": {
                    "type": "string",
                    "description": "Optional amino acid sequence for prediction if PDB not found"
                },
                "include_ligands": {
                    "type": "boolean",
                    "description": "Include bound ligands in the structure (default: true)",
                    "default": True
                }
            },
            "required": ["target"]
        }
    },

    "get_platform_info": {
        "name": "get_platform_info",
        "title": "Get Platform Info",
        "description": "Get NovoMCP platform information including subscription tiers, available tools per tier, database statistics, ADMET capabilities, compliance lists, and credit usage. Use info_type='usage' to see your organization's credit balance and consumption.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "info_type": {
                    "type": "string",
                    "enum": ["all", "tiers", "database", "admet", "compliance", "usage", "update"],
                    "description": "Type of info to retrieve: 'all' (default), 'tiers' (subscription features), 'database' (stats), 'admet' (available predictions), 'compliance' (controlled substance lists), 'usage' (credit balance and consumption), 'update' (current engine version + whether a newer release is available)"
                },
                "org_id": {
                    "type": "string",
                    "description": "Organization ID for usage data (optional - auto-detected from auth)"
                }
            },
            "required": []
        }
    },

    # =========================================================================
    # Enterprise Data Export (unified tool — replaces list_connections,
    # discover_schema, preview_mapping, export_results)
    # =========================================================================
    "push_to_destination": {
        "name": "push_to_destination",
        "title": "Push to Destination",
        "description": "Push tool results to a connected destination such as Google Sheets or BigQuery.",
        "tier": ToolTier.ENTERPRISE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "Action: list_connections, discover_schema, preview_mapping, or export"
                }
            },
            "required": ["action"]
        }
    },

    "pull_from_source": {
        "name": "pull_from_source",
        "title": "Pull from Source",
        "description": "Pull compound data from a connected data warehouse (Snowflake, Databricks), run ADMET/compliance/optimization, and optionally push enriched results back. Actions: preview (inspect table), pull (read rows), estimate_pipeline (cost estimate), execute_pipeline (run full pipeline).",
        "tier": ToolTier.ENTERPRISE,
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["preview", "pull", "estimate_pipeline", "execute_pipeline"],
                    "description": "Action to perform: preview (inspect table metadata), pull (read rows), estimate_pipeline (get cost estimate + confirmation token), execute_pipeline (run full pull→process→push pipeline)"
                },
                "connection_id": {
                    "type": "string",
                    "description": "Connection ID for the source data warehouse"
                },
                "table": {
                    "type": "string",
                    "description": "Table name to read from (e.g., 'compound_library')"
                },
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific columns to select (default: all)"
                },
                "filters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string"},
                            "operator": {"type": "string", "enum": ["=", "!=", ">", "<", ">=", "<=", "IN", "IS_NULL", "IS_NOT_NULL", "LIKE"]},
                            "value": {}
                        },
                        "required": ["column", "operator"]
                    },
                    "description": "Parameterized filters (no raw SQL). Example: [{\"column\": \"status\", \"operator\": \"=\", \"value\": \"active\"}]"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return (Enterprise limit: 10,000)"
                },
                "smiles_column": {
                    "type": "string",
                    "description": "Column containing SMILES strings (auto-detected if not specified)"
                },
                "processing_tools": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["predict_admet", "check_compliance", "optimize_molecule", "calculate_properties"]},
                    "description": "Tools to run on each row's SMILES (for estimate_pipeline and execute_pipeline)"
                },
                "destination_connection_id": {
                    "type": "string",
                    "description": "Connection ID for push destination (optional, for pipeline actions)"
                },
                "destination_table": {
                    "type": "string",
                    "description": "Target table name for push destination (optional)"
                },
                "confirmation_token": {
                    "type": "string",
                    "description": "Token from estimate_pipeline to authorize execute_pipeline"
                }
            },
            "required": ["action"]
        }
    },

    "get_credit_usage": {
        "name": "get_credit_usage",
        "title": "Get Credit Usage",
        "description": "Check your NovoMCP credit balance and research value realized. Shows included credits, overage costs, and billing period. 1 credit = $1. Use when users ask 'How many credits?', 'What's my usage?', 'Summarize my spend', or 'Check my account'.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    # =========================================================================
    # Search & Discovery
    # =========================================================================
    "search_similar": {
        "name": "search_similar",
        "title": "Search Similar Molecules",
        "description": "Find structurally similar molecules by Morgan fingerprint Tanimoto similarity against a configured molecule index. Returns matches with their physicochemical properties; ADMET and compliance blocks attached when those optional services are configured.",
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
                    "minimum": 1,
                    "maximum": 100
                },
                "min_similarity": {
                    "type": "number",
                    "description": "Minimum Tanimoto similarity threshold (0-1)",
                    "default": 0.7,
                    "minimum": 0.0,
                    "maximum": 1.0
                },
                "exclude_controlled": {
                    "type": "boolean",
                    "description": "Exclude DEA controlled substances from results",
                    "default": False
                },
                "exclude_flagged": {
                    "type": "boolean",
                    "description": "Exclude compliance-flagged molecules from results (no-op unless a compliance service is configured)",
                    "default": False
                }
            },
            "required": ["smiles"]
        }
    },

    "filter_molecules": {
        "name": "filter_molecules",
        "title": "Filter Molecules",
        "description": "Filter a configured molecule index by physicochemical property ranges (MW, logP, TPSA, HBD/HBA, rotatable bonds, QED). Returns matches with their properties; ADMET and compliance blocks attached per-molecule when those optional services are configured.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "filters": {
                    "type": "object",
                    "description": "Filter criteria for molecular properties",
                    "properties": {
                        "mw_min": {"type": "number", "description": "Minimum molecular weight (Da)"},
                        "mw_max": {"type": "number", "description": "Maximum molecular weight (Da)"},
                        "logp_min": {"type": "number", "description": "Minimum LogP"},
                        "logp_max": {"type": "number", "description": "Maximum LogP"},
                        "tpsa_max": {"type": "number", "description": "Maximum TPSA (Å²)"},
                        "hbd_max": {"type": "integer", "description": "Maximum H-bond donors (Lipinski: ≤5)"},
                        "hba_max": {"type": "integer", "description": "Maximum H-bond acceptors (Lipinski: ≤10)"},
                        "qed_min": {"type": "number", "description": "Minimum QED drug-likeness score (0-1)"},
                        "toxicity_max": {"type": "number", "description": "Maximum overall toxicity score"},
                        "exclude_controlled": {"type": "boolean", "description": "Exclude DEA controlled substances"},
                        "exclude_pains": {"type": "boolean", "description": "Exclude PAINS compounds"},
                        "exclude_flagged": {"type": "boolean", "description": "Exclude compliance-flagged compounds (no-op unless a compliance service is configured)"}
                    }
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (max 100)",
                    "default": 10,
                    "maximum": 100
                }
            },
            "required": ["filters"]
        }
    },

    "batch_profile": {
        "name": "batch_profile",
        "title": "Batch Profile Molecules",
        "description": "Batch version of get_molecule_profile: RDKit physicochemical properties and structural alerts for up to 100 molecules in one call. ADMET (toxicity, CYP metabolism, nuclear receptors, stress response) and compliance blocks are attached when the corresponding optional services are configured. Set include_admet=false for faster properties-only screening.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "smiles_list": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of SMILES strings (max 100)",
                    "maxItems": 100
                },
                "include_admet": {
                    "type": "boolean",
                    "description": "Include ML ADMET predictions (toxicity, metabolism, etc.) for novel molecules. Default true. Set false for faster, lower-credit properties-only profiling.",
                    "default": True
                }
            },
            "required": ["smiles_list"]
        }
    },

    # =========================================================================
    # Compute — Optimization, Structure, ADMET
    # =========================================================================
    "optimize_molecule": {
        "name": "optimize_molecule",
        "title": "Optimize Molecule",
        "description": "Property-directed molecular optimization using NVIDIA MolMIM (generative AI). Given a seed molecule and target objectives (QED, LogP, TPSA, similarity), generates structurally similar variants biased toward the desired property profile. Returns 3-10 optimized SMILES with property deltas vs seed, each auto-checked for FAVES compliance. Keeps structural similarity high (Tanimoto > 0.4 typical) — for diverse scaffold hopping, use lead_optimization instead.",
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
                    "description": "SMILES string of the molecule to optimize"
                },
                "objectives": {
                    "type": "object",
                    "description": "Optimization objectives",
                    "properties": {
                        "qed": {"type": "number", "description": "Target QED score (0-1)"},
                        "logp": {"type": "number", "description": "Target LogP value"},
                        "sa_score": {"type": "number", "description": "Target synthetic accessibility (1-10, lower is better)"},
                        "similarity": {"type": "number", "description": "Minimum similarity to input (0-1)"}
                    }
                },
                "num_variants": {
                    "type": "integer",
                    "description": "Number of optimized variants to generate (max 50)",
                    "default": 10,
                    "maximum": 50
                },
                "exclude_controlled": {
                    "type": "boolean",
                    "description": "Filter out variants that match controlled substance patterns",
                    "default": True
                },
                "similarity_range": {
                    "type": "object",
                    "description": "Optional Tanimoto similarity window (to seed) for filtering variants. Default 0.3-0.85. Theo's tighter ranges: 0.80-0.85 for SAR-preserving tweaks, 0.75-0.85 for a patent-safe family.",
                    "properties": {
                        "min": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.3},
                        "max": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.85}
                    }
                },
                "patent_risk_thresholds": {
                    "type": "object",
                    "description": "Optional override for patent_risk breakpoints. Defaults {low: 0.4, high: 0.7}.",
                    "properties": {
                        "low": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.4},
                        "high": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.7}
                    }
                }
            },
            "required": ["smiles"]
        }
    },

    "predict_structure": {
        "name": "predict_structure",
        "title": "Predict Protein Structure",
        "description": "Predict 3D protein structure using OpenFold3 with interactive visualization. Supports proteins, DNA, RNA, and protein-ligand complexes. Waits up to 60 seconds for completion. For longer predictions, returns a job_id — use get_structure_result to check progress. Short peptides (<20 residues) typically complete within the wait time. For single-molecule predictions, you may pass a top-level `sequence` (auto-inferred as protein/DNA/RNA from alphabet) or `smiles` (treated as ligand) instead of the full molecules array.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "molecules": {
                    "type": "array",
                    "description": "Molecules to predict structure for (required unless top-level `sequence` or `smiles` is provided)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["protein", "dna", "rna", "ligand"],
                                "description": "Molecule type"
                            },
                            "id": {"type": "string", "description": "Identifier for this molecule"},
                            "sequence": {"type": "string", "description": "Amino acid or nucleotide sequence (for protein/dna/rna)"},
                            "smiles": {"type": "string", "description": "SMILES string (for ligands)"}
                        },
                        "required": ["type", "id"]
                    }
                },
                "sequence": {
                    "type": "string",
                    "description": "Convenience shortcut: single protein/DNA/RNA sequence. Auto-wrapped into molecules=[{type: inferred, id: 'target', sequence}]. Type inferred from alphabet (ACGTU + N → nucleotide, otherwise protein)."
                },
                "smiles": {
                    "type": "string",
                    "description": "Convenience shortcut: single ligand SMILES. Auto-wrapped into molecules=[{type: 'ligand', id: 'ligand_1', smiles}]."
                },
                "output_format": {
                    "type": "string",
                    "enum": ["pdb", "cif"],
                    "default": "pdb"
                }
            }
        }
    },

    "get_structure_result": {
        "name": "get_structure_result",
        "title": "Get Structure Result (Deprecated)",
        "description": "Deprecated — use get_job_status for structure prediction results. Retained for backward compatibility.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job ID from a structure prediction (of3_* prefix)"
                },
                "service": {
                    "type": "string",
                    "enum": ["openfold3", "auto"],
                    "description": "Service (default: openfold3)",
                    "default": "auto"
                }
            },
            "required": ["job_id"]
        }
    },

    "get_job_status": {
        "name": "get_job_status",
        "title": "Get Job Status",
        "description": "Check progress and retrieve results for ANY async NovoMCP job: molecular dynamics (gro_*), docking (dock_*, dock_batch_*), structure prediction (of3_*), quantum (qc_*), lead optimization (lo_*). Returns status, progress percentage, estimated remaining time, and full results when complete. IMPORTANT: If the job is still running, you MUST keep polling every 30-60 seconds until it completes — do NOT give up or treat 'running' as an error.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job ID from the original submission"
                },
                "service": {
                    "type": "string",
                    "enum": ["openfold3", "gromacs", "novo-quantum", "lead-optimization", "autodock-gpu", "auto"],
                    "description": "Service that created the job. Use 'auto' to detect automatically from job_id prefix.",
                    "default": "auto"
                },
                "include_ensemble": {
                    "type": "boolean",
                    "description": "AlphaFlow (af_*) only: include the full multi-model PDB ensemble inline in the response. Default false because the inline payload (~130 KB for a 50-frame run) typically exceeds the MCP inline tool-result soft limit and gets spilled to a file by the client. The slim default returns frame_count + size_bytes + preview + a hint at the /results/<job_id> endpoint the Apps viewer reads directly. Pass true only if you specifically need the bytes inline (e.g. piping to a downstream tool).",
                    "default": False
                }
            },
            "required": ["job_id"]
        }
    },

    "cancel_job": {
        "name": "cancel_job",
        "title": "Cancel Async Job",
        "description": "Cancel a running or queued async job — stop it before it finishes and consumes more credits. Supports gromacs-md jobs (gro_*): deletes the k8s Job on EKS (SIGTERM to the pod), SIGTERM handler writes partial checkpoints to S3, SQL status transitions to cancelling and then to a terminal state (cancelled if SIGTERM caught between stages; failed with the engine's error if SIGTERM caught mid-stage). Use when the user submitted by mistake, the wrong inputs were used, or the job is over budget. Cancelling a completed or failed job is a no-op and returns the current state. Other job types (dock_*, qm_*) will return 'not supported' until their executors implement SIGTERM handling.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job ID from the original submission (e.g. gro_20260514-...)"
                },
                "reason": {
                    "type": "string",
                    "description": "Optional human-readable reason. Persisted in error_message for the audit log.",
                    "default": "Cancelled by user"
                }
            },
            "required": ["job_id"]
        }
    },

    "list_jobs": {
        "name": "list_jobs",
        "title": "List Pipeline Jobs",
        "description": "List async pipeline jobs (MD simulations, docking batches, structure predictions, etc.) with optional status and service filters. Returns job IDs, status, progress, and timestamps. Use to check what jobs are running or recently completed.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "_meta": {
            "ui": {
                "resourceUri": "ui://novomcp/jobs"
            }
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["submitted", "running", "completed", "failed"],
                    "description": "Filter by job status. Omit to return all jobs."
                },
                "service": {
                    "type": "string",
                    "enum": ["gromacs-md", "autodock-gpu", "openfold3", "lead-optimization"],
                    "description": "Filter by service. Omit to return all services."
                },
                "limit": {
                    "type": "integer",
                    "description": "Max jobs to return (default 50, max 100)",
                    "default": 50
                }
            },
            "required": []
        }
    },

    # =========================================================================
    # File Intelligence Layer — upload once, reference everywhere
    # =========================================================================

    "generate_upload_url": {
        "name": "generate_upload_url",
        "title": "Generate File Upload URL",
        "description": "Generate a signed upload URL for large files (QM logs, PDB structures, compound libraries, trajectories). Upload the file directly to the URL — no data flows through the chat. Returns a file_id to reference in downstream tool calls. Free (0 credits). Upload URLs expire in 30 minutes. If auto_process is set, the file will be processed automatically after upload — no need to call a second tool.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Original filename including extension (e.g., '1E67_small_fc.log')"
                },
                "file_type": {
                    "type": "string",
                    "enum": ["qm_log", "pdb", "trajectory", "library", "frcmod", "custom"],
                    "description": "Type of file. Determines allowed extensions and size limits.",
                    "default": "custom"
                },
                "auto_process": {
                    "type": "object",
                    "description": "Optional: auto-trigger a tool when upload completes. Set tool name and args. The file_id is injected automatically into the field specified by inject_as (default: qm_file_id). User gets email notification when processing finishes.",
                    "properties": {
                        "tool": {"type": "string", "description": "Tool to auto-run (e.g., 'parameterize_metal', 'audit_system', 'run_molecular_dynamics')"},
                        "args": {"type": "object", "description": "Arguments for the tool (file_id is injected into the inject_as field)"},
                        "inject_as": {"type": "string", "description": "Field name to inject the file_id as. Default: 'qm_file_id'. Use 'pdb_content_file_id' for PDB uploads to audit_system/parameterize_metal Phase 1.", "default": "qm_file_id"}
                    }
                }
            },
            "required": ["filename"]
        }
    },

    "get_file_status": {
        "name": "get_file_status",
        "title": "Get File Status",
        "description": "Check the status of an uploaded file — pending, uploaded, processing, completed. Shows linked tool calls, job IDs, and processing results. Use after uploading a file to verify it's ready for tool consumption.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "File ID returned by generate_upload_url (e.g., 'f-abc123...')"
                }
            },
            "required": ["file_id"]
        }
    },

    "list_files": {
        "name": "list_files",
        "title": "List Uploaded Files",
        "description": "List files uploaded to your account. Filter by file type (qm_log, pdb, trajectory, library) or status (uploaded, processing, completed). Shows file IDs, names, sizes, and linked jobs.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_type": {
                    "type": "string",
                    "enum": ["qm_log", "pdb", "trajectory", "library", "frcmod", "custom"],
                    "description": "Filter by file type. Omit to return all types."
                },
                "status": {
                    "type": "string",
                    "enum": ["pending_upload", "uploaded", "processing", "completed", "expired"],
                    "description": "Filter by status. Omit to return all statuses."
                },
                "limit": {
                    "type": "integer",
                    "description": "Max files to return (default 50)",
                    "default": 50
                }
            },
            "required": []
        }
    },

    "get_pipeline_audit": {
        "name": "get_pipeline_audit",
        "title": "Get Pipeline Audit Log",
        "description": "Retrieve the per-molecule audit trail for a completed pipeline execution. Shows what happened to each molecule: input SMILES, standardization, per-tool results (ADMET, compliance, properties), disposition (included/excluded), and exclusion reasons. Use for GxP compliance documentation.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "pipeline_id": {
                    "type": "string",
                    "description": "Pipeline ID (e.g., pipe_abc123)"
                }
            },
            "required": ["pipeline_id"]
        }
    },

    "run_novo_ag": {
        "name": "run_novo_ag",
        "title": "'Novo AG' / 'agm' → Autonomous Mode Trigger",
        "description": "TRIGGER KEYWORDS: 'Novo AG', 'novo ag', 'novoag', 'agm', 'AGM' (case-insensitive; slash-prefixed forms like '/agm' may be intercepted by MCP clients as slash commands and won't reach the model — recommend 'agm' without the slash). Call ONLY when the user's message contains one of these literal tokens. Returns the 11-stage autonomous discovery instruction set — read it and follow it. For messages without these keywords, operate in normal collaborative mode (use tools as needed, ask clarifying questions). Pass mode='interactive' for the human-in-the-loop variant. Cost: 0 credits.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "disease": {
                    "type": "string",
                    "description": "Disease or indication to target (e.g., 'acute myeloid leukemia', 'glioblastoma', 'lung adenocarcinoma'). Required."
                },
                "mode": {
                    "type": "string",
                    "enum": ["autonomous", "interactive"],
                    "description": "'autonomous' (default) runs end-to-end without stops. 'interactive' pauses at each stage for user review.",
                    "default": "autonomous"
                },
                "md_duration_ns": {
                    "type": "number",
                    "description": "MD simulation duration in nanoseconds. Default 1 (short, ~2 min). Increase for production runs.",
                    "default": 1
                }
            },
            "required": ["disease"]
        }
    },

    "save_funnel_stage": {
        "name": "save_funnel_stage",
        "title": "Save Funnel Stage",
        "description": "Record a HUMAN-REVIEWED checkpoint decision in the discovery funnel. **You do NOT need to call this to log tool calls — every tool call is already auto-logged server-side as an 'exploration' event under the session funnel_id. NEVER ask the user whether to log; logging is automatic and client-agnostic.** Call save_funnel_stage ONLY to capture the user's explicit decision/approval at a reviewed checkpoint in an interactive funnel (pass human_reviewed: true with human_decision + human_prompt) — that human context is the one thing the auto-log cannot capture. For checkpoint events inside the canonical 11-stage funnel, pass `funnel_stage` (1-11). Do NOT pass `stage_index` — it is a monotonic event counter the server auto-assigns per funnel_id. stage_name defaults to the tool name. See docs/AGENTMODE-ARCHITECTURE.md §1 for the canonical stage table.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "funnel_id": {"type": "string", "description": "Unique funnel run ID (defaults to MCP session ID)"},
                "event_type": {
                    "type": "string",
                    "enum": ["checkpoint", "exploration"],
                    "description": "'checkpoint' = formal human-reviewed funnel stage. 'exploration' = ad-hoc tool call during ideation/backtracking. Defaults to 'checkpoint'.",
                    "default": "checkpoint"
                },
                "stage_index": {"type": "integer", "description": "Monotonic event counter per funnel_id. Auto-assigned server-side if omitted (recommended)."},
                "funnel_stage": {"type": "integer", "minimum": 1, "maximum": 12, "description": "Canonical funnel stage 1-12 (see docs/AGENTMODE-ARCHITECTURE.md §1). Required for checkpoint events; omit for ad-hoc/exploration events outside the funnel."},
                "stage_name": {"type": "string", "description": "Stage identifier (e.g., target_discovery). Defaults to tool_name."},
                "stage_label": {"type": "string", "description": "Human-readable label (e.g., Target Discovery). Defaults to titleized stage_name."},
                "tool_name": {"type": "string", "description": "MCP tool that was called"},
                "tool_arguments": {"type": "object", "description": "Arguments passed to the tool"},
                "results_summary": {"type": "object", "description": "Key results from the tool"},
                "ai_recommendation": {"type": "string", "description": "What the AI recommended"},
                "human_decision": {"type": "string", "description": "What the user decided"},
                "human_prompt": {"type": "string", "description": "The user's actual message"},
                "decision_reasoning": {"type": "string", "description": "Why the user made this decision"},
                "human_reviewed": {"type": "boolean", "description": "Whether a human reviewed this stage. True for interactive funnel checkpoints, False for auto-logged exploration events."},
                "molecules_in": {"type": "integer", "description": "Molecules entering this stage"},
                "molecules_out": {"type": "integer", "description": "Molecules leaving this stage"},
                "molecules_filtered": {"type": "object", "description": "Breakdown of filtered molecules by reason"},
                "system_metadata": {"type": "object", "description": "System prep details (water count, box dims, pocket info)"},
                "curation_method": {"type": "object", "description": "Library curation filters, order, thresholds"},
                "credits_consumed": {"type": "number", "description": "Credits used at this stage"},
                "execution_time_ms": {"type": "integer", "description": "Wall clock time in ms"},
                "context_forward": {"type": "object", "description": "State carried to next stage"},
                "source_file_id": {"type": "string", "description": "File ID from the file intelligence layer, if this stage used an uploaded file"}
            },
            "required": ["funnel_id"]
        }
    },

    "get_funnel_audit": {
        "name": "get_funnel_audit",
        "title": "Get Funnel Audit Log",
        "description": "Retrieve the reproducibility log for a discovery funnel run. Shows every event (checkpoints + exploration tool calls) with tool arguments, result summaries, AI recommendations, human decisions, molecule counts, filtering details, and compute costs. Filter by event_type='checkpoint' for a clean peer-review view that excludes ad-hoc exploration.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "funnel_id": {
                    "type": "string",
                    "description": "Funnel run ID to retrieve"
                },
                "event_type": {
                    "type": "string",
                    "enum": ["checkpoint", "exploration", "all"],
                    "description": "Filter events. 'checkpoint' = formal funnel stages only (peer-review view). 'exploration' = ad-hoc tool calls only. 'all' = full trail (default).",
                    "default": "all"
                }
            },
            "required": ["funnel_id"]
        }
    },

    "list_funnels": {
        "name": "list_funnels",
        "title": "List Discovery Funnels",
        "description": "List recent discovery funnel runs with metadata — disease, target gene, outcome, stage count, credits consumed, best affinity. Use this to find a funnel_id before calling get_funnel_audit. Returns the most recent funnels for the current org, enriched with terminal summary data when available.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max funnels to return (default 15)",
                    "default": 15
                },
                "target_gene": {
                    "type": "string",
                    "description": "Filter by target gene (e.g., 'EGFR', 'FLT3')"
                },
                "outcome": {
                    "type": "string",
                    "enum": ["SUCCEEDED", "FAILED_NO_LEADS", "FAILED_TOXICITY", "FAILED_POTENCY", "ABANDONED"],
                    "description": "Filter by funnel outcome"
                }
            }
        }
    },

    "save_funnel_memory": {
        "name": "save_funnel_memory",
        "title": "Save Funnel Memory (terminal summary)",
        "description": "Write a terminal summary of a completed discovery funnel to cross-run memory. Called once at the end of a funnel run (typically after Stage 11 completion). Captures target metadata, outcome, failure patterns, key decisions, and a natural-language summary for analogical retrieval. Powers search_prior_runs — future funnels targeting the same gene/disease can learn from this run's outcome and avoid repeating mistakes. Semantic embedding generated automatically from the summary via Azure OpenAI text-embedding-3-large.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "funnel_id": {"type": "string", "description": "Funnel run ID this memory belongs to"},
                "target_gene": {"type": "string", "description": "Target gene symbol (e.g., KRAS, EGFR)"},
                "target_pdb_id": {"type": "string", "description": "PDB ID used for docking"},
                "therapeutic_area": {"type": "string", "description": "Therapeutic area / indication (e.g., 'lung adenocarcinoma')"},
                "chemotype": {"type": "string", "description": "Chemotype or scaffold class explored"},
                "outcome": {
                    "type": "string",
                    "enum": ["SUCCEEDED", "FAILED_BUDGET", "FAILED_MAX_ITER", "FAILED_REDLINE", "FAILED_CRITICAL", "ABANDONED"],
                    "description": "Terminal outcome of the funnel"
                },
                "final_lead_count": {"type": "integer", "description": "Number of lead candidates that survived to the end"},
                "best_affinity_kcal": {"type": "number", "description": "Best binding affinity observed (kcal/mol, negative = better)"},
                "failure_pattern": {"type": "object", "description": "JSON describing what failed and why (e.g., {\"compliance_block\": 0.8, \"reason\": \"scaffold similarity to banned molecules\"})"},
                "decisions": {"type": "object", "description": "JSON capturing key decisions made during the run (pivots, threshold adjustments, scaffold choices)"},
                "summary": {"type": "string", "description": "Natural-language summary (2-4 sentences) of the run, its outcome, and the lesson. Used for semantic search."},
                "perturbation_channel_active": {"type": "boolean", "description": "Whether the Phase 0 perturbation evidence channel was active during this funnel's target_discovery call (PERTURBATION_CHANNEL_ENABLED). Optional; omit if the funnel did not call target_discovery."},
                "perturbation_changed_top3": {"type": "boolean", "description": "Whether the perturbation channel changed the top-3 target ranking vs the channel-off counterfactual. Optional; omit if the counterfactual wasn't computed for this funnel."},
                "perturbation_channel_coverage": {
                    "type": "string",
                    "enum": ["ok", "degraded_no_coverage", "disabled", "unknown"],
                    "description": "Coverage status reported by the perturbation channel for the queried disease. 'degraded_no_coverage' = FM 13 (channel enabled but no rows for disease, e.g. CNS in Phase 0). Optional."
                }
            },
            "required": ["funnel_id", "outcome", "summary"]
        }
    },

    "search_prior_runs": {
        "name": "search_prior_runs",
        "title": "Search Prior Discovery Runs",
        "description": "Query cross-run memory for past discovery funnels that targeted the same gene, PDB, or therapeutic area. Returns terminal summaries, outcomes, and lessons from prior attempts. Call at funnel start to learn from precedents — avoid repeating known failure modes, reuse successful scaffolds, calibrate threshold expectations. Includes a lazy backstop that auto-generates template summaries for completed funnels that lack explicit memory entries, ensuring cross-run coverage is complete.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_gene": {"type": "string", "description": "Target gene symbol to search (e.g., KRAS)"},
                "target_pdb_id": {"type": "string", "description": "PDB ID to search"},
                "therapeutic_area": {"type": "string", "description": "Therapeutic area / indication to search"},
                "outcome": {
                    "type": "string",
                    "enum": ["SUCCEEDED", "FAILED_BUDGET", "FAILED_MAX_ITER", "FAILED_REDLINE", "FAILED_CRITICAL", "ABANDONED", "any"],
                    "description": "Filter by outcome. 'any' (default) returns all.",
                    "default": "any"
                },
                "query": {"type": "string", "description": "Natural-language query for semantic search over summaries (uses vector similarity when embeddings are available)"},
                "max_results": {"type": "integer", "description": "Maximum results to return (default 10, max 50)", "default": 10}
            }
        }
    },

    "get_3d_properties": {
        "name": "get_3d_properties",
        "title": "Get 3D Properties",
        "description": "Get 3D molecular properties from conformer generation. Returns 32+ properties including geometry (radius of gyration, asphericity, PMI), energy (conformer energy, VDW, electrostatic, strain), electrostatics (dipole moment, partial charges), surface/volume (SASA, molecular volume, globularity), and full 3D coordinates.",
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
                    "description": "SMILES string of the molecule"
                },
                "force_field": {
                    "type": "string",
                    "enum": ["AMBER", "CHARMM", "OPLS", "GROMOS"],
                    "description": "Force field for energy calculations",
                    "default": "AMBER"
                },
                "optimize_3d": {
                    "type": "boolean",
                    "description": "Whether to optimize the 3D geometry",
                    "default": True
                },
                "include_coordinates": {
                    "type": "boolean",
                    "description": "Include full 3D atomic coordinates in response",
                    "default": True
                }
            },
            "required": ["smiles"]
        }
    },

    "calculate_properties": {
        "name": "calculate_properties",
        "title": "Calculate Properties",
        "description": "Calculate RDKit molecular properties on-demand. Returns Lipinski descriptors, drug-likeness scores (QED, SA_Score), physicochemical properties (LogP, TPSA, MW), and structural features. Does NOT include ADMET or compliance — use get_molecule_profile for a complete analysis.",
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
                    "description": "SMILES string of the molecule"
                },
                "include_fingerprints": {
                    "type": "boolean",
                    "description": "Include molecular fingerprints (Morgan/ECFP)",
                    "default": False
                }
            },
            "required": ["smiles"]
        }
    },

    "predict_admet": {
        "name": "predict_admet",
        "title": "Predict ADMET",
        "description": "Predict toxicity and ADMET properties: cardiotoxicity, hepatotoxicity, nephrotoxicity, carcinogenicity, CYP450 inhibition (1A2/2C9/2C19/2D6/3A4 substrate + inhibitor), nuclear receptor activity (AR/ER/PR/GR/PPAR), stress response (p53, oxidative stress), absorption, distribution, metabolism, excretion. Returns per-model probabilities with severity categories. 40+ ML models from addie-models backend. Normally called automatically by get_molecule_profile; use directly for ADMET-only queries.",
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
                    "description": "SMILES string of the molecule"
                },
                "models": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific ADMET models to run (default: all). Options: hepatotoxicity, cardiotoxicity, cyp450_1a2, cyp450_2c9, cyp450_2c19, cyp450_2d6, cyp450_3a4, herg, bbb, caco2, pgp, vdss, clearance, half_life, bioavailability, ames, skin_sensitization"
                }
            },
            "required": ["smiles"]
        }
    },

    "predict_clinical_outcomes": {
        "name": "predict_clinical_outcomes",
        "title": "Predict Clinical Outcomes",
        "description": (
            "Predict Phase I clinical trial clearance probability for a small molecule. "
            "Automatically gathers all 63 required features by orchestrating chem-props "
            "(physicochemical), faves-compliance (structural alerts, BOILED-Egg), and "
            "addie-models (ADMET) in parallel, then calls the NovoExpert v3 model. "
            "Returns a calibrated probability, SHAP feature explanations, and a "
            "domain-specific competence assessment. The model is validated for "
            "CARDIOVASCULAR and mainstream compounds (AUROC 0.72-0.76) but NOT for "
            "oncology, CNS, or infectious disease domains (near-random performance). "
            "Check the competence_check in the response before acting on predictions."
        ),
        "tier": ToolTier.CORE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "smiles": {
                    "type": "string",
                    "description": "SMILES string of the molecule to evaluate"
                },
                "therapeutic_area": {
                    "type": "string",
                    "description": "Therapeutic area for competence assessment. Options: ONCOLOGY, CARDIOVASCULAR, CNS_NEURO, INFECTIOUS, METABOLIC, IMMUNO_INFLAM, RENAL_GU, RESPIRATORY, GI, PAIN_ANALGESIA, ENDOCRINE, OPHTH_DERM, OTHER, UNKNOWN",
                    "default": "UNKNOWN"
                },
                "target_type": {
                    "type": "string",
                    "description": "Target type. Options: SINGLE PROTEIN, PROTEIN FAMILY, PROTEIN COMPLEX, NUCLEIC-ACID, ORGANISM, CELL-LINE, SMALL MOLECULE, UNKNOWN",
                    "default": "UNKNOWN"
                },
                "action_type": {
                    "type": "string",
                    "description": "Mechanism of action. Options: INHIBITOR, ANTAGONIST, AGONIST, BLOCKER, ACTIVATOR, MODULATOR, PARTIAL AGONIST, SUBSTRATE, RELEASING AGENT, UNKNOWN",
                    "default": "UNKNOWN"
                },
                "top_k_shap": {
                    "type": "integer",
                    "description": "Number of top SHAP features to return (1-63)",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 63
                }
            },
            "required": ["smiles"]
        }
    },

    "search_literature": {
        "name": "search_literature",
        "title": "Search Literature",
        "description": "Find published journal articles and research papers on drug discovery topics. Searches 14,398 curated peer-reviewed publications via Pinecone semantic search. Returns paper titles, abstracts, authors, DOIs, and relevance scores. Covers ADMET research, target validation, medicinal chemistry, SAR studies, and clinical pharmacology. Use for literature review, prior art assessment, and evidence gathering during target evaluation.",
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
                    "description": "Search query (e.g., 'EGFR inhibitor selectivity', 'hepatotoxicity prediction')"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of papers to return (max 20)",
                    "default": 10,
                    "maximum": 20
                },
                "year_min": {
                    "type": "integer",
                    "description": "Minimum publication year filter"
                }
            },
            "required": ["query"]
        }
    },

    "search_patents": {
        "name": "search_patents",
        "title": "Search Patents",
        "description": "Search granted and pending USPTO pharmaceutical patent filings (2,416 documents, Pinecone semantic search). Returns patent titles, abstracts, applicants, filing dates, and relevance scores. Use for intellectual property landscape analysis, freedom-to-operate assessment, prior art search, and competitive intelligence on patented scaffolds or mechanisms.",
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
                    "description": "Search query (e.g., 'kinase inhibitor', 'antibody drug conjugate')"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of patents to return (max 20)",
                    "default": 10,
                    "maximum": 20
                },
                "year_min": {
                    "type": "integer",
                    "description": "Minimum filing year filter"
                }
            },
            "required": ["query"]
        }
    },

    # =========================================================================
    # Research Databases (External APIs)
    # =========================================================================
    "search_biorxiv": {
        "name": "search_biorxiv",
        "title": "Search bioRxiv",
        "description": "Find pre-publication preprints from bioRxiv and medRxiv (live API query, not curated — searches all recent preprints). Returns preprint titles, abstracts, authors, DOIs, and publication dates. Useful for finding cutting-edge research before formal peer review, recent findings on emerging targets, and early-stage results not yet in the published literature. Complements search_literature which covers peer-reviewed publications only.",
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
                    "description": "Search query (e.g., 'CRISPR drug delivery', 'mRNA vaccine')"
                },
                "server": {
                    "type": "string",
                    "enum": ["biorxiv", "medrxiv"],
                    "default": "biorxiv",
                    "description": "Preprint server to search"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (max 30)",
                    "default": 10,
                    "maximum": 30
                },
                "days_back": {
                    "type": "integer",
                    "description": "Search papers from last N days (default: 365)",
                    "default": 365
                }
            },
            "required": ["query"]
        }
    },

    "search_chembl": {
        "name": "search_chembl",
        "title": "Search ChEMBL",
        "description": "Measured bioactivity data from ChEMBL — 2.4M compounds with assay activities, targets, IC50/Ki values. Returns compound structures, target information, and activity values. Search by compound, target, or activity type.",
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
                    "description": "Search query - compound name, target name, or disease"
                },
                "search_type": {
                    "type": "string",
                    "enum": ["compound", "target", "activity"],
                    "default": "compound",
                    "description": "Type of search: compound (molecules), target (proteins), or activity (bioassay data)"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (max 25)",
                    "default": 10,
                    "maximum": 25
                }
            },
            "required": ["query"]
        }
    },

    "search_clinical_trials": {
        "name": "search_clinical_trials",
        "title": "Search Clinical Trials",
        "description": "Registered clinical trial records from ClinicalTrials.gov, including recruitment status and trial phase (live API query). Returns trial titles, phases, status, conditions, interventions, sponsors, and enrollment numbers. Essential for competitive intelligence and indication research.",
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
                    "description": "Search terms — drug names, targets, sponsors, keywords. Use with 'condition' for best results."
                },
                "condition": {
                    "type": "string",
                    "description": "Disease or condition (e.g., 'lung adenocarcinoma'). Maps to ClinicalTrials.gov condition field for precise matching."
                },
                "status": {
                    "type": "string",
                    "enum": ["RECRUITING", "ACTIVE_NOT_RECRUITING", "COMPLETED", "TERMINATED", "ALL"],
                    "default": "ALL",
                    "description": "Trial status filter"
                },
                "phase": {
                    "type": "string",
                    "enum": ["PHASE1", "PHASE2", "PHASE3", "PHASE4", "ALL"],
                    "default": "ALL",
                    "description": "Clinical phase filter"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of trials to return (max 25)",
                    "default": 10,
                    "maximum": 25
                }
            }
        }
    },

    # =========================================================================
    # Compliance & Screening
    # =========================================================================
    "check_compliance": {
        "name": "check_compliance",
        "title": "Check Compliance",
        "description": "Check regulatory and compliance status against DEA (controlled substances), FDA (drug approval), EPA (environmental/pesticide), EU REACH (chemical registration), CWC (chemical weapons convention), BTWC (biological weapons convention), OPCW (international chemical weapons treaty), and Australia Schedule. Context-dependent assessment keyed on intended_use + jurisdiction + therapeutic_area — returns PROCEED / STOP / CAUTION with risk factors, regulatory pathway, and jurisdiction-specific recommendations. **Whitelist override:** FDA-approved compounds (the V3 whitelist — cortisol, dopamine, aspirin, ibuprofen, etc.) return overall_status=PROCEED even when V4 structural alerts fire; alerts surface as informational context in `structural_alert_summary` but do not change the verdict. **Response shape:** top-level `overall_status`, `base_compliance` (regulatory + whitelist flags), and `recommendations`; FAVES V4 fields (per-catalog alert counts in `structural_alert_summary`, BOILED-Egg PK in `pk_classification`, prior-art disclosure in `prior_art`, full V3 detection in `faves_v3`) live under `context_compliance.base_classification.*`.",
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
                    "description": "SMILES string of the molecule"
                },
                "context": {
                    "type": "object",
                    "description": "Context for compliance evaluation (REQUIRED). Determines which agencies and treaty frameworks apply (DEA, FDA, CWC, EPA, EU REACH, BTWC, Australia Schedule, OPCW).",
                    "properties": {
                        "intended_use": {
                            "type": "string",
                            "enum": ["pharmaceutical", "research", "industrial", "agricultural", "cosmetic"],
                            "description": "Primary intended use — routes to different regulatory frameworks: pharmaceutical (FDA IND/NDA, EMA), research (laboratory/academic, DEA Schedule I exceptions), industrial (REACH, OSHA), agricultural (EPA pesticide, FIFRA), cosmetic (FDA cosmetic, EU CPR)."
                        },
                        "jurisdiction": {
                            "type": "string",
                            "enum": ["US", "EU", "UK", "CA", "AU", "JP", "CN", "GLOBAL"],
                            "description": "Regulatory jurisdiction. US=DEA+FDA+EPA, EU=EMA+EU REACH+EMCDDA, UK=MHRA, CA=Health Canada, AU=TGA+Australia Schedule, JP=PMDA, CN=NMPA, GLOBAL=CWC+BTWC+OPCW international treaties (chemical and biological weapons conventions)."
                        },
                        "therapeutic_area": {
                            "type": "string",
                            "description": "Therapeutic area (e.g., oncology, cardiology, neurology, immunology, infectious_disease, metabolic, rare_disease)."
                        },
                        "target_population": {
                            "type": "string",
                            "description": "Target patient population (e.g., pediatric, geriatric, general, pregnant, immunocompromised). Affects FDA special-population considerations."
                        },
                        "manufacturing_scale": {
                            "type": "string",
                            "enum": ["lab", "pilot", "commercial"],
                            "description": "Manufacturing scale. lab (<1kg, R&D exemptions), pilot (1-100kg, GMP scale-up), commercial (>100kg, full GMP + REACH registration thresholds)."
                        }
                    },
                    "required": ["intended_use", "jurisdiction"]
                }
            },
            "required": ["smiles", "context"]
        }
    },

    "screen_library": {
        "name": "screen_library",
        "title": "Screen Library",
        "description": "Screen up to 1,000 molecules for drug-likeness and structural alerts (PAINS, Brenk) in one call. Returns RDKit physicochemical properties and structural alerts always; ADMET predictions and regulatory compliance are attached when the corresponding optional services are configured. When compliance is configured, optionally pass intended_use + jurisdiction + therapeutic_area for context-dependent regulatory assessment (DEA, FDA, EU REACH). Use for HTS triage, library QC, or pre-docking filtering.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "smiles_list": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of SMILES strings to screen (max 1000)",
                    "maxItems": 1000
                },
                "context": {
                    "type": "object",
                    "description": "Optional context for compliance (if provided, runs context-dependent check)",
                    "properties": {
                        "intended_use": {"type": "string"},
                        "jurisdiction": {"type": "string"},
                        "therapeutic_area": {"type": "string"}
                    }
                },
                "output_format": {
                    "type": "string",
                    "enum": ["summary", "full", "flagged_only"],
                    "default": "summary"
                }
            },
            "required": ["smiles_list"]
        }
    },

    # =========================================================================
    # Property Prediction (pKa, Solubility, BDE)
    # =========================================================================
    "predict_pka": {
        "name": "predict_pka",
        "title": "Predict pKa",
        "description": "Predict acid dissociation constant (pKa) for a molecule. Identifies ionizable functional groups (carboxylic acids, amines, phenols, sulfonamides, etc.) and returns pKa values. Critical for understanding drug absorption, formulation pH, and charge state at physiological pH. Returns ionizable groups detected and confidence level.",
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
                    "description": "SMILES string of the molecule"
                }
            },
            "required": ["smiles"]
        }
    },

    "predict_solubility": {
        "name": "predict_solubility",
        "title": "Predict Aqueous Solubility",
        "description": "Predict aqueous solubility as LogS (log10 mol/L) with optional temperature dependence. Returns LogS value, solubility in mg/mL, and a category (highly_soluble, soluble, slightly_soluble, insoluble). Essential for formulation development and oral bioavailability assessment. Default temperature is 25C (298.15K).",
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
                    "description": "SMILES string of the molecule"
                },
                "temperature_k": {
                    "type": "number",
                    "description": "Temperature in Kelvin (default 298.15 = 25C). Range: 273-373K.",
                    "default": 298.15
                }
            },
            "required": ["smiles"]
        }
    },

    "predict_bde": {
        "name": "predict_bde",
        "title": "Predict Bond Dissociation Energy",
        "description": "Predict homolytic bond dissociation energies (BDE) in kcal/mol for all C-H bonds in a molecule using the alfabet model. Identifies the weakest bond — useful for predicting metabolic soft spots, radical reactivity, and oxidative stability. Lower BDE = more susceptible to hydrogen abstraction by CYP enzymes.",
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
                    "description": "SMILES string of the molecule"
                }
            },
            "required": ["smiles"]
        }
    },

    # =========================================================================
    # QM Compute (xTB, CREST, Strain)
    # =========================================================================
    "run_qm_calculation": {
        "name": "run_qm_calculation",
        "title": "Run QM Calculation",
        "description": "Run a semi-empirical quantum mechanics calculation (GFN2-xTB) on a molecule. Supports single-point energy, geometry optimization, and solvation energy (ALPB model). Returns electronic energy, HOMO-LUMO gap, dipole moment, and optionally optimized geometry. Supports charged species (charge) and open-shell systems (uhf). Pass xyz_input to bypass SMILES-to-3D conversion and use a pre-optimized geometry (e.g., for redox thermodynamic cycles).",
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
                    "description": "SMILES string of the molecule"
                },
                "calculation": {
                    "type": "string",
                    "enum": ["energy", "optimize", "solvation"],
                    "description": "Calculation type. energy=single-point electronic energy at input geometry, optimize=geometry optimization to nearest minimum, solvation=solvation free energy via ALPB implicit solvent model.",
                    "default": "optimize"
                },
                "charge": {
                    "type": "integer",
                    "description": "Molecular charge. 0=neutral closed-shell (default), +1=cation, -1=anion, +2/-2=dication/dianion. Required for charged species, radical ions, protonated amines, deprotonated carboxylates, and redox thermodynamic cycles (oxidation = charge+1 uhf+1, reduction = charge-1 uhf+1).",
                    "default": 0
                },
                "uhf": {
                    "type": "integer",
                    "description": "Number of unpaired electrons. 0=singlet closed-shell (default), 1=doublet (radical, radical cation, radical anion, open-shell transition metal d1/d9), 2=triplet (O2, carbene, triplet excited state). Required for correct open-shell energies; neutral radicals and redox-generated radical ions must set uhf=1.",
                    "default": 0
                },
                "solvent": {
                    "type": "string",
                    "description": "Solvent for ALPB implicit solvation model. Accepts: water, methanol, ethanol, acetone, acetonitrile, dmso, dmf, chloroform, dichloromethane, thf, toluene, benzene, hexane, ether. Omit for gas-phase calculation.",
                    "default": "water"
                },
                "xyz_input": {
                    "type": "string",
                    "description": "Pre-optimized XYZ geometry (Cartesian coordinates, Angstroms). Bypasses SMILES-to-3D conversion. Use for thermodynamic cycles (e.g., vertical IP/EA at a fixed geometry for redox potential calculations), transition state follow-up, or reusing a geometry from a prior optimization."
                }
            },
            "required": ["smiles"]
        }
    },

    "run_qm_hessian": {
        "name": "run_qm_hessian",
        "title": "Vibrational Frequencies & Thermochemistry",
        "description": "Compute vibrational frequencies, normal modes, and thermochemistry via xTB Hessian calculation. Returns all vibrational frequencies (cm⁻¹), explicitly flags imaginary frequencies (negative values indicating the structure is not a true minimum — it's a transition state or saddle point), plus zero-point energy (ZPE), enthalpy correction, Gibbs free energy correction (ΔG = ΔH - TΔS), and entropy. Essential for reaction thermodynamics and verifying optimized geometries are minima. Tip: pass xyz_input from a prior optimization for meaningful thermochemistry at the true minimum, or set optimize_first=true to optimize then run Hessian in one call.",
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
                    "description": "SMILES string of the molecule"
                },
                "charge": {
                    "type": "integer",
                    "description": "Molecular charge (default 0)",
                    "default": 0
                },
                "uhf": {
                    "type": "integer",
                    "description": "Number of unpaired electrons (0=singlet, 1=doublet for radicals/ions)",
                    "default": 0
                },
                "solvent": {
                    "type": "string",
                    "description": "Solvent for ALPB solvation model (e.g., water, dmso, acetonitrile)"
                },
                "temperature": {
                    "type": "number",
                    "description": "Temperature in K for thermochemistry (default 298.15)",
                    "default": 298.15
                },
                "xyz_input": {
                    "type": "string",
                    "description": "Pre-optimized XYZ geometry. Recommended for meaningful thermochemistry."
                },
                "optimize_first": {
                    "type": "boolean",
                    "description": "If true, optimize geometry before Hessian (--ohess). If false, run Hessian at given geometry (--hess).",
                    "default": False
                }
            },
            "required": ["smiles"]
        }
    },

    "predict_frontier_orbitals": {
        "name": "predict_frontier_orbitals",
        "title": "Frontier Orbital Analysis (OLED/Optoelectronics)",
        "description": "Predict frontier orbital properties for OLED and optoelectronics screening. Returns HOMO, LUMO, gap, emission wavelength, emission color (UV/blue/green/yellow/red/IR), triplet energy, and OLED suitability classification (phosphorescent emitter, fluorescent emitter, charge transport, host material). Detects OLED-relevant functional groups (carbazole, triphenylamine, anthracene, oxadiazole, Ir/Pt complexes, etc.). Uses GFN2-xTB for orbital energies + empirical calibration for emission prediction.",
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
                    "description": "SMILES string of the molecule or material"
                },
                "solvent": {
                    "type": "string",
                    "description": "Solvent environment (e.g., toluene, chloroform, water). Affects orbital energies via ALPB solvation."
                }
            },
            "required": ["smiles"]
        }
    },

    "run_excited_states": {
        "name": "run_excited_states",
        "title": "Excited State Calculation (sTDA-xTB)",
        "description": "Compute excited states using the simplified Tamm-Dancoff Approximation (sTDA) with xTB. Returns singlet and triplet excitation energies (eV), wavelengths (nm), oscillator strengths, S1/T1 energies, and singlet-triplet gap. More accurate than HOMO-LUMO gap for emission prediction. Use for OLED design, fluorescence/phosphorescence screening, and photochemistry. Takes 10-30 seconds.",
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
                    "description": "SMILES string of the molecule"
                },
                "charge": {
                    "type": "integer",
                    "description": "Molecular charge (default 0)",
                    "default": 0
                },
                "num_states": {
                    "type": "integer",
                    "description": "Number of excited states to compute (default 10)",
                    "default": 10
                },
                "xyz_input": {
                    "type": "string",
                    "description": "Pre-optimized XYZ geometry (recommended for accurate excited states)"
                }
            },
            "required": ["smiles"]
        }
    },

    "predict_redox_potential": {
        "name": "predict_redox_potential",
        "title": "Electrolyte Redox Potential Screening",
        "description": "Predict oxidation and reduction potentials for battery electrolyte design. Uses a GFN2-xTB thermodynamic cycle (neutral → cation → anion optimization) with ALPB solvation. Returns adiabatic and vertical ionization potential (IP) and electron affinity (EA), electrode potentials vs reference electrode (SHE, Li/Li+, Ag/AgCl, SCE, Fc/Fc+), and stability classification against lithium-ion (0-4.2V), high-voltage Li-ion (0-5V), aqueous (0-1.23V), and sodium-ion windows. Takes 30-90 seconds per molecule. Screening-grade accuracy (±0.3-0.5V) — use for candidate ranking, not final selection.",
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
                    "description": "SMILES string of the electrolyte molecule"
                },
                "solvent": {
                    "type": "string",
                    "description": "Solvent for ALPB implicit solvation. Must be one of xTB's supported ALPB strings: water (default), acetonitrile, methanol, acetone, dmso, dmf, thf, dioxane, ether, ethylacetate, ch2cl2, chcl3, benzene, toluene, hexane, nitromethane, phenol, aniline. Battery carbonates (ethylene carbonate, propylene carbonate, EMC, DMC) are NOT in xTB's ALPB set — passing 'ethylene_carbonate' or similar crashes xtb with exit 128. For electrolyte redox, use 'water' as a polar stand-in; the carbonate SMARTS calibration class (0.318 V MAE) recovers most of the missing solvent shift.",
                    "default": "water"
                },
                "reference_electrode": {
                    "type": "string",
                    "enum": ["SHE", "Li/Li+", "Ag/AgCl", "SCE", "Fc/Fc+"],
                    "description": "Reference electrode for reporting potentials. Use Li/Li+ for battery work, SHE for general electrochemistry.",
                    "default": "SHE"
                }
            },
            "required": ["smiles"]
        }
    },

    "predict_reaction_thermodynamics": {
        "name": "predict_reaction_thermodynamics",
        "title": "Reaction Thermodynamics (ΔG, ΔH, K_eq)",
        "description": "Predict whether a chemical reaction is thermodynamically feasible. Takes reactant and product SMILES, computes ΔE, ΔH, ΔG (Gibbs free energy), TΔS (entropy contribution), and equilibrium constant K_eq. Spontaneous if ΔG < 0. Uses GFN2-xTB with Hessian for zero-point energy and thermal corrections on each species. Confidence flag: high for organic reactions, low for transition metal catalysis (validate with DFT). No transition state search — thermodynamics only, not kinetics. Takes 60-180 seconds (Hessian per species).",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "reactant_smiles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "SMILES strings of the reactants"
                },
                "product_smiles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "SMILES strings of the products"
                },
                "solvent": {
                    "type": "string",
                    "description": "Solvent for ALPB solvation (e.g., water, thf, dmso)"
                },
                "temperature": {
                    "type": "number",
                    "description": "Temperature in K (default 298.15)",
                    "default": 298.15
                }
            },
            "required": ["reactant_smiles", "product_smiles"]
        }
    },

    "find_transition_state": {
        "name": "find_transition_state",
        "title": "Transition State Search (NEB)",
        "description": "Find the transition state and activation barrier between a reactant and product using Nudged Elastic Band (NEB) with GFN2-xTB. Returns activation energy (forward + reverse barriers), transition state geometry, and the minimum energy pathway. Requires pre-optimized reactant and product XYZ geometries (use run_qm_calculation with calculation='optimize' first). **Bimolecular reactions require a pre-built van der Waals complex** as the reactant — NEB cannot path between two separate molecules and will return non-convergent unphysical barriers (e.g., 500+ kcal/mol). For unimolecular isomerizations (e.g., HCN→HNC, conformational changes), feed the two minimum geometries directly. For bimolecular reactions (Diels-Alder, SN2, etc.), build a reactant complex with the two molecules in proximity and run optimization on the complex first. Always check the `converged` flag and `warnings` list — if `converged: false`, the barrier is unreliable. Takes 2-10 minutes depending on molecule size and number of images. Adds kinetics ('how fast?') to thermodynamics ('is it feasible?'). Use predict_reaction_thermodynamics first to confirm the reaction is thermodynamically favorable, then find_transition_state for the kinetic barrier.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "reactant_xyz": {
                    "type": "string",
                    "description": "XYZ geometry of the optimized reactant (from run_qm_calculation optimize)"
                },
                "product_xyz": {
                    "type": "string",
                    "description": "XYZ geometry of the optimized product (from run_qm_calculation optimize)"
                },
                "n_images": {
                    "type": "integer",
                    "description": "Number of intermediate NEB images (default 8, more = smoother path but slower)",
                    "default": 8
                },
                "charge": {
                    "type": "integer",
                    "description": "Molecular charge (default 0)",
                    "default": 0
                },
                "uhf": {
                    "type": "integer",
                    "description": "Unpaired electrons (default 0)",
                    "default": 0
                },
                "solvent": {
                    "type": "string",
                    "description": "ALPB solvent model (e.g., water, thf)"
                }
            },
            "required": ["reactant_xyz", "product_xyz"]
        }
    },

    "run_conformer_search": {
        "name": "run_conformer_search",
        "title": "Conformer Search",
        "description": "Generate a conformer ensemble for a molecule using CREST (GFN2-xTB) or RDKit ETKDG. Returns ranked conformers with Boltzmann populations and relative energies. Essential before docking — ensures you dock the bioactive conformer, not just the lowest-energy one. IMPORTANT ASYNC BEHAVIOR: This returns a job_id immediately. CREST takes 5-15 minutes. You MUST tell the user the estimated time and ask them to say 'check job [job_id]' when ready. Do NOT auto-poll in a loop — you will hit tool call limits. Poll at most 2-3 times per conversation turn. Progress stays at 10% during computation — this is NORMAL, not a stall. Never report it as stuck or broken while status is 'running'.",
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
                    "description": "SMILES string of the molecule"
                },
                "max_conformers": {
                    "type": "integer",
                    "description": "Maximum number of conformers to return (default 20, max 100)",
                    "default": 20,
                    "maximum": 100
                },
                "energy_window": {
                    "type": "number",
                    "description": "Energy window in kcal/mol — conformers within this range of the global minimum are kept",
                    "default": 6.0
                },
                "quick": {
                    "type": "boolean",
                    "description": "Use quick mode for faster but less thorough search",
                    "default": False
                }
            },
            "required": ["smiles"]
        }
    },

    "dock_with_strain": {
        "name": "dock_with_strain",
        "title": "Strain-Corrected Docking Score",
        "description": "Calculate the internal strain energy of a docked ligand pose using xTB. Strain = E(docked_pose) - E(optimized). High strain (>5 kcal/mol) indicates the docking score may be an artifact — the ligand is forced into an unnatural conformation. Use after dock_molecules to filter false positives. Returns strain energy and interpretation.",
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
                    "description": "SMILES string of the ligand"
                },
                "docked_xyz": {
                    "type": "string",
                    "description": "XYZ coordinates of the docked pose (from dock_molecules output). If not provided, generates 3D coords from SMILES."
                }
            },
            "required": ["smiles"]
        }
    },

    # =========================================================================
    # Neural Network Potentials (ANI-2x, MACE)
    # =========================================================================
    "compute_energy": {
        "name": "compute_energy",
        "title": "Compute Energy (Neural Potential)",
        "description": "Compute molecular energy and atomic forces using neural network potentials. ~100x faster than xTB. Models: ANI-2x (organic molecules: H/C/N/O/F/S/Cl, most drug-like compounds), MACE-MP-0 (universal, all elements). Use 'auto' to select the best model automatically. Returns energy in eV and kcal/mol, plus max/RMS force magnitudes. Useful for fast conformer ranking, strain estimation, and batch energetics screening.",
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
                    "description": "SMILES string of the molecule"
                },
                "method": {
                    "type": "string",
                    "enum": ["auto", "ani2x", "mace"],
                    "description": "Neural potential model. 'auto' selects ANI-2x for organic molecules, MACE for others.",
                    "default": "auto"
                }
            },
            "required": ["smiles"]
        }
    },

    "search_materials_project": {
        "name": "search_materials_project",
        "title": "Search Materials Project Database",
        "description": "Search the Materials Project database for known inorganic materials. Returns band gap, formation energy, energy above hull (stability), crystal structure, and material ID. Search by chemical formula (e.g., 'LiCoO2'), chemical system (e.g., 'Li-Co-O'), or material ID (e.g., 'mp-22526'). Useful for: checking if a cathode/anode material is known, looking up band gaps for semiconductors, finding thermodynamically stable compositions. Note: Materials Project covers inorganic/solid-state materials, NOT organic molecules — use search_similar or check_compliance for organic compound lookup.",
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
                    "description": "Chemical formula (LiCoO2, LiFePO4), chemical system (Li-Fe-O, Li-Co-O), or Materials Project ID (mp-22526)"
                },
                "search_type": {
                    "type": "string",
                    "enum": ["formula", "chemsys", "material_id"],
                    "description": "Search type: formula (exact formula), chemsys (chemical system / element combination), material_id (specific MP ID)",
                    "default": "formula"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 5)",
                    "default": 5
                }
            },
            "required": ["query"]
        }
    },

    "optimize_geometry_nnp": {
        "name": "optimize_geometry_nnp",
        "title": "Geometry Optimization (Neural Potential)",
        "description": "Atomic geometry refinement via neural network potentials with ASE BFGS. For structure relaxation of atomic coordinates — not property-directed compound optimization (see lead_optimization or optimize_molecule for that). ~100x faster than xTB geometry optimization. Models: ANI-2x (organic, H/C/N/O/F/S/Cl), MACE-MP-0 (universal). Returns relaxed XYZ, final energy, convergence status, step count. LIMITATION: neutral molecules only — charged species (charge≠0) and open-shell (uhf≠0) must use run_qm_calculation instead (xTB supports charge/spin, NNPs do not).",
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
                    "description": "SMILES string of the molecule"
                },
                "method": {
                    "type": "string",
                    "enum": ["auto", "ani2x", "mace"],
                    "description": "Neural potential model",
                    "default": "auto"
                },
                "fmax": {
                    "type": "number",
                    "description": "Force convergence threshold in eV/Å (default 0.05)",
                    "default": 0.05
                },
                "charge": {
                    "type": "integer",
                    "description": "Molecular charge (must be 0 — NNPs do not support charged species; use run_qm_calculation for charged molecules)",
                    "default": 0
                },
                "uhf": {
                    "type": "integer",
                    "description": "Unpaired electrons (must be 0 — NNPs do not support open-shell; use run_qm_calculation for radicals)",
                    "default": 0
                }
            },
            "required": ["smiles"]
        }
    },

    # =========================================================================
    # Omics-Driven Discovery (Stage 1 + Stage 11)
    # =========================================================================
    "target_discovery": {
        "name": "target_discovery",
        "title": "Omics-Driven Target Discovery",
        "description": "Identify and rank drug targets for a disease using pre-computed multi-omics evidence (genetics, expression, tractability) blended with a perturbation evidence channel from public Perturb-seq (signature reversal). Queries 108K target-disease associations from Open Targets + TCGA expression data plus the omics.omics_perturbation table; per-target evidence_channels breakdown is returned for audit. Returns ranked targets with composite scores, suggested PDB structures for docking, and pathway context. Use this as Stage 1 (of the 11-stage discovery funnel) before search_literature to start with a genetically validated target.",
        "tier": ToolTier.TEAM,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "disease": {
                    "type": "string",
                    "description": "Disease name or EFO ID (e.g. 'lung adenocarcinoma', 'EFO_0000571')"
                },
                "tissue": {
                    "type": "string",
                    "description": "Optional tissue context for filtering (e.g. 'lung', 'liver')"
                },
                "min_evidence": {
                    "type": "number",
                    "description": "Minimum overall association score (0-1)",
                    "default": 0.5,
                    "minimum": 0,
                    "maximum": 1
                },
                "max_targets": {
                    "type": "integer",
                    "description": "Maximum number of targets to return",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50
                }
            },
            "required": ["disease"]
        }
    },
    "validate_target": {
        "name": "validate_target",
        "title": "Adversarial Target Validation",
        "description": "Stress-test a drug target hypothesis before committing compute credits. "
                       "Searches for both supporting AND contradicting evidence: failed clinical trials, "
                       "known resistance mechanisms, off-target toxicity signals, competitive landscape. "
                       "Synthesizes a 0-1 confidence score from 4 weighted evidence streams: "
                       "clinical trials (3×), ChEMBL bioactivity (2×), literature (1×), omics (1×). "
                       "Classifies target maturity (mature_validated / emerging / novel) and returns a "
                       "recommendation (proceed / proceed_with_caution / reconsider) with specific "
                       "risk factors and strengths. Essential adversarial checkpoint: 'Is this target "
                       "worth pursuing, or will it fail for known reasons?' Use after target_discovery.",
        # FREE, not the defunct PRO tier: normalize_tier() collapses pro→free, so
        # the executor already treats this as free. Tagging it PRO only desynced
        # tools/list (which doesn't normalize) — it hid the tool from free users
        # who could nonetheless execute it. See router.list_tools tier gating.
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Gene symbol (e.g. 'EGFR', 'BRAF') or protein name to validate"
                },
                "disease": {
                    "type": "string",
                    "description": "Disease name (e.g. 'glioblastoma', 'non-small cell lung cancer') or EFO/MONDO ID"
                },
                "skip_cache": {
                    "type": "boolean",
                    "description": "Bypass the 1-hour result cache for this (target, disease) key. Use when re-running after a relevance-threshold tuning or other server-side change so the test exercises the fresh evidence pipeline. Default false. The response always carries `_cached: true` when it came from cache, so callers can detect stale results without this flag.",
                    "default": False
                }
            },
            "required": ["target", "disease"]
        }
    },
    "stratify_patients": {
        "name": "stratify_patients",
        "title": "Patient Stratification & Pharmacogenomics",
        "description": "Assess clinical viability of a drug candidate through pharmacogenomic profiling and resistance mutation analysis. Cross-references the candidate's CYP metabolism profile (from ADMET predictions) against population-level pharmacogene frequencies, and checks for known resistance mutations in the target gene. Returns population coverage estimates, PGx risk alleles, resistance variants, and clinical viability assessment. Use this as Stage 11 (of the 11-stage discovery funnel) after molecular dynamics validation. The target_gene is validated against the HGNC registry before any lookup — unknown symbols return a structured error with a suggestion (for aliases/previous symbols) or an HGNC search URL (for truly unknown genes). Valid HGNC genes outside the 56-pharmacogene panel return clinical_viability='not_applicable' instead of a silent empty response.",
        "tier": ToolTier.TEAM,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "smiles": {
                    "type": "string",
                    "description": "Candidate molecule SMILES"
                },
                "target_gene": {
                    "type": "string",
                    "description": "Target gene symbol — validated against the HGNC registry (44K official symbols + 58K aliases accepted). Examples: EGFR, BRAF, KRAS, CYP2D6, HER2 (auto-resolved to ERBB2). Unknown symbols return a structured error with suggestions (for aliases/previous symbols) or an HGNC search URL (for truly unknown genes). Valid HGNC genes outside the 56-pharmacogene panel return clinical_viability='not_applicable' instead of silent failure. Also accepts legacy alias 'gene_symbol'."
                },
                "indication": {
                    "type": "string",
                    "description": "Disease indication for context"
                },
                "admet_results": {
                    "type": "object",
                    "description": "ADMET predictions from Stage 5 (containing CYP substrate probabilities). If omitted, tool will attempt to retrieve from funnel context."
                },
                "include_pgx": {
                    "type": "boolean",
                    "description": "Include pharmacogenomic analysis",
                    "default": True
                },
                "include_biomarkers": {
                    "type": "boolean",
                    "description": "Include resistance mutation analysis",
                    "default": True
                }
            },
            "required": ["smiles", "target_gene"]
        }
    },

    # =========================================================================
    # Pipeline Compute Tools (Steps 6-8)
    # =========================================================================
    "lead_optimization": {
        "name": "lead_optimization",
        "title": "Lead Optimization",
        "description": "Generate structurally diverse molecular variants via scaffold hopping (RDKit substructure replacement, 30+ ring pairs) or property-directed optimization. Returns enriched variants with SA scores, ADMET predictions, compliance checks, Tanimoto-to-seed similarity, and patent risk classification per variant. Auto-filters controlled/flagged compounds. For high-similarity property optimization close to the seed, use optimize_molecule (MolMIM) instead — this tool produces broader chemical diversity. Use after ADMET screening and compliance check to generate candidates for docking. Note: fused polycyclic scaffolds (acridine, carbazole, naphthalene, xanthene) may return 0 variants due to RDKit sanitization limitations — the response includes a diagnostic. Credits are refunded when 0 variants are returned.",
        "tier": ToolTier.TEAM,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "smiles": {
                    "type": "string",
                    "description": "Seed molecule SMILES to optimize"
                },
                "optimization_type": {
                    "type": "string",
                    "enum": ["scaffold_hop", "property_directed"],
                    "default": "scaffold_hop",
                    "description": "Optimization strategy"
                },
                "num_variants": {
                    "type": "integer",
                    "description": "Number of variants to generate (1-50). Also accepts legacy alias 'max_variants'.",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 50
                },
                "target_properties": {
                    "type": "object",
                    "description": "Optional target property values (mw, logp, qed, tpsa)"
                },
                "similarity_range": {
                    "type": "object",
                    "description": "Optional Tanimoto similarity window (to seed) for filtering variants. Variants outside the range are dropped before enrichment. Theo's guidance: 0.80-0.85 preserves SAR predictability around a lead; 0.75-0.85 for a patent-safe family. Default 0.3-0.85 is broad enough to not filter anything by default while still excluding identical matches.",
                    "properties": {
                        "min": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.3},
                        "max": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.85}
                    }
                },
                "patent_risk_thresholds": {
                    "type": "object",
                    "description": "Optional override for patent_risk classification breakpoints. Variants with Tc >= high are tagged 'high' (same patent family risk), Tc between low and high are 'low' (patentable scaffold hop), Tc < low are 'novel' (highly novel, verify pharmacophore). Defaults: {low: 0.4, high: 0.7}.",
                    "properties": {
                        "low": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.4},
                        "high": {"type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.7}
                    }
                }
            },
            "required": ["smiles"]
        }
    },
    "dock_molecules": {
        "name": "dock_molecules",
        "title": "Dock Molecules",
        "description": "Dock molecules against a protein target using AutoDock-GPU. Single-molecule (smiles_list with 1 entry): executes directly, no confirmation needed, returns docking results synchronously. Batch (2+ molecules): two-phase — first call returns cost estimate + confirmation_token; second call with token submits a batch job and returns job_id (poll get_job_status every 30s until completed). Credits: 10 base + 5 per molecule (single = 15, batch of 4 = 30). GPU COLD START: the first call after idle (>5 min) includes ~2-3 min GPU warm-up; subsequent calls within 5 min are fast. If called within a discovery funnel, target_discovery already triggered a background warmup — the GPU should be ready by Step 6.",
        "tier": ToolTier.TEAM,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "smiles_list": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of SMILES strings to dock (max 100)",
                    "maxItems": 100
                },
                "protein_pdb_id": {
                    "type": "string",
                    "description": "PDB ID of target protein (e.g. '6OIM'). Use suggested_pdb_id from target_discovery."
                },
                "exhaustiveness": {
                    "type": "integer",
                    "description": "Search exhaustiveness (8-32, higher = more accurate but slower)",
                    "default": 16,
                    "minimum": 8,
                    "maximum": 32
                },
                "num_modes": {
                    "type": "integer",
                    "description": "Number of binding modes to generate per molecule",
                    "default": 9,
                    "minimum": 1,
                    "maximum": 20
                },
                "confirmation_token": {
                    "type": "string",
                    "description": "Token from a previous dock_molecules call to confirm and execute the docking job. Omit on first call to get a cost estimate."
                },
                "protonation_ph": {
                    "type": "number",
                    "description": "pH for ligand and receptor protonation state (default 7.4 = blood plasma / physiological). Affects ionizable groups: amines (protonated <pKa), carboxylates (deprotonated >pKa), imidazoles, phenols, phosphates. Compartment presets: 1-3 stomach/gastric, 5-7 intestinal, 4.5 lysosomal, 6.5 tumor microenvironment, 7.4 blood/plasma/cytosol.",
                    "default": 7.4,
                    "minimum": 1.0,
                    "maximum": 14.0
                },
                "funnel_id": {
                    "type": "string",
                    "description": "Optional conversation-level funnel ID. Stored alongside the async job so get_funnel_context can return it and a resuming session can rehydrate the full audit trail via get_funnel_audit."
                },
                "reference_ligand_smiles": {
                    "type": "string",
                    "description": "Optional reference ligand SMILES for co-docking benchmark. If omitted, the co-crystallized ligand is auto-extracted from the PDB (largest non-buffer HETATM). Each candidate pose gets a delta_vs_reference_kcal field so affinity can be interpreted against a known binder instead of in isolation."
                },
                "enable_reference_docking": {
                    "type": "boolean",
                    "description": "If true (default), dock a reference ligand and report delta_vs_reference_kcal on each pose. Set false to skip (saves ~50% runtime, but affinity will only be comparable across candidates in the same batch).",
                    "default": True
                }
            },
            "required": ["smiles_list", "protein_pdb_id"]
        }
    },
    "audit_system": {
        "name": "audit_system",
        "title": "Audit System (Free)",
        "description": "Pre-flight check for molecular dynamics: classify a protein structure without running MD. Returns a structured report: membrane detection (OPM), metal sites with coordination and functional role (MetalPDB + Pfam), heme/Fe-S clusters, and a routing verdict (run_soluble or refused with a specific reason and suggested_branch). Use BEFORE run_molecular_dynamics to qualify targets — if would_route_to='refused', MD will refuse with the same reason and the submission will be wasted. Free (0 credits). Returns within ~5 seconds. Accepts either pdb_id (RCSB lookup) or pdb_content (direct upload).",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "openWorldHint": True
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "pdb_id": {
                    "type": "string",
                    "description": "4-character PDB ID (e.g. '1CA2', '2RH1'). Fetched from RCSB."
                },
                "pdb_content": {
                    "type": "string",
                    "description": "Raw PDB file contents. Use when the target isn't in RCSB (AlphaFold model, user-modified structure)."
                }
            }
        }
    },

    "parameterize_metal": {
        "name": "parameterize_metal",
        "title": "Parameterize Metal Site",
        "description": "ASYNC JOB: Two-phase metal parameterization via MCPB.py for metalloprotein MD simulation. Returns mcpb_ job_id immediately — poll get_job_status every 60s until completed. Phase 1 (no qm_log_content): extracts coordination fragment around the metal site, generates Gaussian/ORCA .com input files, returns a confirmation_token. The user runs Gaussian/ORCA on those .com files externally. Phase 2 (confirmation_token + QM logs): processes the QM output → extracts force constants (Seminario method) + RESP charges → produces AMBER .frcmod/.prep and GROMACS .top/.gro (registered as downloadable child files of the input log). **Two-log requirement (Gaussian):** the Hessian and the MK ESP charges come from two SEPARATE runs — run Phase 1's small_fc.com (freq → Hessian) and large_mk.com (Pop(MK) → ESP), then pass hessian_file_id + esp_file_id (upload each via generate_upload_url). A single combined log via qm_file_id/qm_log_content is accepted only for the rare case where one file holds both sections (e.g. a .fchk). **Workflow constraint:** the logs must come from running Gaussian on Phase 1's specific .com files — atom indexing must match. Pre-existing QM logs from custom cluster models will fail with atom-mapping errors. Always run Phase 1 first. Auto-process pattern: upload the Hessian log, then upload the ESP log with auto_process={tool:'parameterize_metal', args:{hessian_file_id:<id>, confirmation_token:<tok>}, inject_as:'esp_file_id'} to trigger Phase 2 on the second upload. Use audit_system first to identify the metal site and verify the resid before Phase 1. Limitation: processes one chain at a time — for multi-chain metals (bridging sites in oligomers), extract chains separately. CPU-only, Phase 1 ~1-2 min, Phase 2 ~2-5 min.",
        "tier": ToolTier.ENTERPRISE,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "pdb_id": {
                    "type": "string",
                    "description": "PDB ID of the metalloprotein (e.g., '1CA2', '1E67')"
                },
                "metal_resid": {
                    "type": "integer",
                    "description": "Residue number of the metal to parameterize"
                },
                "qm_log_content": {
                    "type": "string",
                    "description": "Phase 2 only: inline contents of the Gaussian .log. For large files, use qm_file_id instead."
                },
                "qm_file_id": {
                    "type": "string",
                    "description": "Phase 2 (legacy single-log): file ID from generate_upload_url for one combined QM log. Prefer hessian_file_id + esp_file_id for Gaussian (a single run can't hold both the Hessian and MK ESP)."
                },
                "hessian_file_id": {
                    "type": "string",
                    "description": "Phase 2 (preferred): file ID of the small_fc (freq) log carrying the Hessian / force constants. Pair with esp_file_id."
                },
                "esp_file_id": {
                    "type": "string",
                    "description": "Phase 2 (preferred): file ID of the large_mk (Pop(MK)) log carrying the ESP charges for RESP fitting. Pair with hessian_file_id."
                },
                "confirmation_token": {
                    "type": "string",
                    "description": "Phase 2 only: token from Phase 1 linking to the workspace. Omit for Phase 1."
                },
                "qm_software": {
                    "type": "string",
                    "enum": ["gaussian", "orca"],
                    "description": "QM engine",
                    "default": "gaussian"
                },
                "charge": {
                    "type": "integer",
                    "description": "Total charge of the QM fragment",
                    "default": 0
                },
                "multiplicity": {
                    "type": "integer",
                    "description": "Spin multiplicity",
                    "default": 1
                }
            },
            "required": ["pdb_id", "metal_resid"]
        }
    },

    "run_molecular_dynamics": {
        "name": "run_molecular_dynamics",
        "title": "Run Molecular Dynamics",
        "description": "Run GPU molecular dynamics simulation using GROMACS. Returns a job_id — use get_job_status to poll for results every 60s until completed. Estimated runtime is included in the response. Omit pdb_id for ligand-only. Results include RMSD, RMSF, radius of gyration. When a ligand is present and the system is soluble (not membrane), results ALSO include: MM-GBSA binding free energy (ΔG_bind) with per-residue decomposition, ligand RMSD, protein-ligand H-bond persistence (with quality threshold: >75% High Quality, <30% False Positive Risk), and pose clustering. Membrane systems skip MM-GBSA (standard GB/PB solver is incorrect with a lipid bilayer) but still get ligand dynamics. GPU COLD START: the first call after idle (>5 min) includes ~2-3 min GPU warm-up before the job actually starts; subsequent calls are fast. If called within a discovery funnel, target_discovery already triggered a background warmup — the GPU should be ready by Step 7.",
        "tier": ToolTier.ENTERPRISE,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "smiles": {
                    "type": "string",
                    "description": "Ligand SMILES string"
                },
                "pdb_id": {
                    "type": "string",
                    "description": "Target protein PDB ID. Omit for ligand-only simulation."
                },
                "duration_ns": {
                    "type": "number",
                    "description": "Simulation length in nanoseconds (1-100). Default 10ns (~20 min on A100).",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 100
                },
                "temperature": {
                    "type": "number",
                    "description": "Simulation temperature in Kelvin",
                    "default": 300
                },
                "funnel_id": {
                    "type": "string",
                    "description": "Optional conversation-level funnel ID. Stored alongside the async job so get_funnel_context can return it and a resuming session can rehydrate the full audit trail via get_funnel_audit."
                },
                "intent": {
                    "type": "string",
                    "enum": ["smoke_test", "equilibration_only", "pose_stability", "mm_gbsa"],
                    "description": "Scientific intent of the simulation. Drives the use-case-specific quality grade in the result's three-layer quality_report (execution_integrity / sampling_quality / scientific_adequacy). When set, the action message and remediation highlight that intent. When omitted, all known intents are still graded so the report stays informative. Choose 'smoke_test' for plumbing checks, 'equilibration_only' to validate system setup before longer production, 'pose_stability' for ligand-pocket binding-pose claims (≥10ns standard), 'mm_gbsa' for binding-energy decomposition (≥50ns standard, requires protein-ligand complex)."
                },
                "adaptive_equilibration": {
                    "type": "boolean",
                    "description": "Opt-in: replace the fixed 100 ps NPT stage with an adaptive loop that extends NPT in 100 ps blocks (initial 50 ps + extensions up to a 1 ns cap) until water density plateau is detected via first-half vs second-half mean comparison. Adds 0–1 ns to total runtime depending on system. Default false. Recommended for protein-ligand complexes where the 100 ps fixed window often leaves density still drifting; the adaptive log appears in result.equilibration.npt_adaptive with iteration count, cumulative duration, and convergence flag.",
                    "default": False
                }
            },
            "required": ["smiles"]
        }
    },

    "generate_dynamics": {
        "name": "generate_dynamics",
        "title": "Generate Conformational Dynamics",
        "description": "Generate AI-accelerated conformational ensemble from a protein structure using AlphaFlow/ESMFlow. Shows how a protein moves — loops swaying, domains shifting, binding pockets opening/closing. Returns a job_id — use get_job_status to poll for results every 60s. IMPORTANT: This is an async job. The AlphaFlow model is large and may take 1-2 minutes to warm up on first use after a cold start. If you get a 503 'Model not loaded' error, wait 2 minutes and retry — the model is still loading. Typical inference: 1-5 minutes depending on protein size. Results include multi-model PDB for trajectory animation, per-residue RMSF (flexibility), and PCA of conformational variation.",
        "tier": ToolTier.ENTERPRISE,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "pdb_id": {
                    "type": "string",
                    "description": "PDB ID of the protein (e.g., '1M17')"
                },
                "pdb_data": {
                    "type": "string",
                    "description": "PDB file content as string (alternative to pdb_id)"
                },
                "sequence": {
                    "type": "string",
                    "description": "Amino acid sequence (alternative to pdb_id/pdb_data)"
                },
                "n_frames": {
                    "type": "integer",
                    "description": "Number of conformations to generate (5-500, default 50). Higher = more detail but longer runtime.",
                    "default": 50,
                    "minimum": 5,
                    "maximum": 500
                },
                "funnel_id": {
                    "type": "string",
                    "description": "Optional conversation-level funnel ID. Stored alongside the async job so get_funnel_context can return it and a resuming session can rehydrate the full audit trail via get_funnel_audit."
                }
            },
            "required": []
        }
    },

    # =========================================================================
    # Funnel Context Persistence
    # =========================================================================
    "save_funnel_context": {
        "name": "save_funnel_context",
        "title": "Save Funnel Context",
        "description": "Persist discovery pipeline state (target gene, seed molecule, optimization results, docking scores) before an async job starts. Called automatically by dock_molecules and run_molecular_dynamics before returning a job_id. Write-only — cannot retrieve state. Use get_funnel_context to read it back when resuming a pipeline in a new session.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Async job ID (dock_xxx or gro_xxx)"
                },
                "service": {
                    "type": "string",
                    "description": "Service name (autodock-gpu, gromacs-md)"
                },
                "context": {
                    "type": "object",
                    "description": "Funnel state JSON — target_gene, candidates, scores, prior step results"
                }
            },
            "required": ["job_id", "context"]
        }
    },
    "get_funnel_context": {
        "name": "get_funnel_context",
        "title": "Get Funnel Context",
        "description": "Retrieve saved pipeline state for an async job. Read-only — cannot write state. Use when resuming a discovery funnel after a docking or MD job completes in a new session. Returns the full funnel state (target gene, seed, candidates, scores, prior step results) from the session that submitted the job. Use save_funnel_context to persist state.",
        "tier": ToolTier.FREE,
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False
        },
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Async job ID to retrieve context for"
                }
            },
            "required": ["job_id"]
        }
    }
}

# Merge tree-guided retrieval tool definitions
MCP_TOOLS.update(TREE_SEARCH_TOOLS)


# =============================================================================
# TOOL SURFACE ASSIGNMENT
# -----------------------------------------------------------------------------
# Two MCP servers share this single MCP_TOOLS registry, distinguished only by
# the request Host header:
#   Novo          (ai.novomcp.com)      -> core surface, all tiers
#   Novo Compute  (compute.novomcp.com) -> GPU/quantum surface, paid tiers
#
# Source of truth for the split: docs/NovoMCP/Product/TOOL-INVENTORY.md
# (per-tool Y/Compute columns). Pre-AWS-migration this lived in
# novomcp-apps/server.ts via `isCompute` blocks; the novomcp consolidation
# dropped it, so both hosts were serving the full superset. This restores the
# split at the registry layer; routers filter via visible_tools()/host_is_compute().
#
# Three buckets, validated below to exactly partition MCP_TOOLS:
#   compute -> only on Novo Compute
#   core    -> only on Novo (the residual; not enumerated as a set)
#   both    -> on both surfaces (platform/audit/file/context/memory infra)
# =============================================================================

# Compute-only: GPU/quantum/MD/QM/NNP/materials/structure + async job tools.
COMPUTE_ONLY_TOOLS = {
    "get_3d_properties",
    "predict_pka", "predict_solubility", "predict_bde",
    "run_qm_calculation", "run_conformer_search", "dock_with_strain", "run_qm_hessian",
    "predict_frontier_orbitals", "predict_redox_potential",
    "predict_reaction_thermodynamics", "run_excited_states",
    "search_materials_project", "find_transition_state",
    "optimize_geometry_nnp", "compute_energy",
    "dock_molecules", "run_molecular_dynamics", "parameterize_metal",
    "get_protein_structure", "predict_structure", "get_structure_result",
    "generate_dynamics",
    "list_jobs", "get_job_status", "cancel_job",
}

# Shared: platform/audit/funnel/file/context/cross-run-memory infrastructure.
# Not tier- or compute-specific; reachable from either surface.
SHARED_TOOLS = frozenset({
    "get_platform_info",
    "save_funnel_stage", "get_funnel_audit", "get_pipeline_audit",
    "save_funnel_context", "get_funnel_context",
    "generate_upload_url", "get_file_status", "list_files",
    "audit_system", "run_novo_ag", "save_funnel_memory", "search_prior_runs",
})


def _surface_for(tool_name: str) -> str:
    """Resolve a tool's surface bucket: 'compute', 'both', or 'core' (residual)."""
    if tool_name in COMPUTE_ONLY_TOOLS:
        return "compute"
    if tool_name in SHARED_TOOLS:
        return "both"
    return "core"


# Stamp every registered tool with its surface, and validate that the classifier
# sets exactly partition the live registry so drift is loud, not silent:
#   - a set referencing a tool that no longer exists  -> stale reference
#   - a tool in BOTH a compute and shared set         -> programming error
# (Unclassified tools legitimately fall through to 'core', so the large
#  core-only bucket is intentionally not enumerated.)
for _name, _tool in MCP_TOOLS.items():
    _tool["surface"] = _surface_for(_name)

_stale_compute = COMPUTE_ONLY_TOOLS - set(MCP_TOOLS)
_stale_shared = SHARED_TOOLS - set(MCP_TOOLS)
_double_classified = COMPUTE_ONLY_TOOLS & SHARED_TOOLS
if _stale_compute or _stale_shared or _double_classified:
    logger.warning(
        "Tool surface map drift: stale_compute=%s stale_shared=%s double_classified=%s",
        sorted(_stale_compute), sorted(_stale_shared), sorted(_double_classified),
    )

logger.info(
    "Tool surfaces: %d compute-only, %d shared, %d core-only (total %d)",
    len(COMPUTE_ONLY_TOOLS), len(SHARED_TOOLS),
    len(MCP_TOOLS) - len(COMPUTE_ONLY_TOOLS) - len(SHARED_TOOLS), len(MCP_TOOLS),
)


def host_is_compute(host: Optional[str]) -> bool:
    """True when the request Host targets the Novo Compute surface.

    Mirrors the detection in main_https.py so both the info banner and the
    tool list agree on which surface a request hit.
    """
    h = (host or "").lower()
    return h.startswith("compute.") or ".compute." in h


def is_tool_visible(tool_name: str, is_compute_surface: bool) -> bool:
    """Whether a tool is exposed on the surface the request landed on."""
    surface = MCP_TOOLS.get(tool_name, {}).get("surface", "core")
    if surface == "compute":
        return is_compute_surface
    if surface == "core":
        return not is_compute_surface
    return True  # 'both'


# --- Unified REST API surface (api.novomcp.com) ------------------------------
# The two MCP connectors (ai./compute.novomcp.com) split tools by Host. The REST
# API is a single host that exposes ALL tools; compute tools are gated by paid
# tier instead of by host — mirroring the ncmcp_ compute-key paywall enforced by
# dashboard-aggregator's validate-compute-key (COMPUTE_TIERS). This is required
# because most COMPUTE_ONLY_TOOLS carry a FREE per-tool tier (the paywall lives
# on the key/host, not the tool), so per-tool tier alone would leak them.
PAID_TIERS = frozenset({"core", "team", "enterprise"})

REST_API_HOSTS = frozenset(
    h.strip().lower()
    for h in os.getenv("REST_API_HOSTS", "api.novomcp.com").split(",")
    if h.strip()
)


def host_is_rest_api(host: Optional[str]) -> bool:
    """True when the request Host is the unified REST API surface."""
    h = (host or "").split(":")[0].lower()
    return h in REST_API_HOSTS


def rest_tool_visible(tool_name: str, user_tier: str) -> bool:
    """Gate for the unified REST surface: all tools visible, but compute-only
    tools require a paid tier. Per-tool ToolTier checks still apply on top."""
    surface = MCP_TOOLS.get(tool_name, {}).get("surface", "core")
    if surface == "compute":
        return user_tier in PAID_TIERS
    return True  # core + shared always reachable on the REST surface


# =============================================================================
# Local-availability tool filter (OSS v1 clean tools/list)
# =============================================================================
# Tools whose dependencies aren't wired locally are hidden from tools/list —
# users only see what actually works. As we ship data + service walkthroughs
# in future releases (per product-roadmap.md), tools re-appear automatically
# when their required env vars / files / services show up.
#
# Set `NOVOMCP_SHOW_HIDDEN_TOOLS=1` to override the filter and see everything
# (useful for developers + internal debugging).

# Map of tool_name → list of "requirements" (env vars, file paths, etc.).
# A tool is hidden if ANY of its requirements is unmet. Empty list = always
# visible. Requirements formats:
#   "env:VARNAME"    → hidden if os.getenv(VARNAME) is empty
#   "file:PATH"      → hidden if the file doesn't exist
#   "any"            → always available (no dependency)
TOOL_LOCAL_REQUIREMENTS: Dict[str, list] = {
    # Enterprise data connectors — subscription-only, hidden from OSS entirely.
    # Roadmap: never (kept as hosted-only tools).
    "push_to_destination": ["env:NEVER_SHIP_IN_OSS"],
    "pull_from_source":    ["env:NEVER_SHIP_IN_OSS"],

    # Omics tools — need Postgres + omics schema loaded. Roadmap: v1.1.x SQLite bundle.
    "target_discovery": ["env:NOVOMCP_DB_HOST"],
    "stratify_patients": ["env:NOVOMCP_DB_HOST"],
    "validate_target":   ["env:NOVOMCP_DB_HOST"],

    # Funnel-persistence tools — need audit/credit-ledger backend.
    # Roadmap: v1.8.x reference implementation.
    "save_funnel_stage":    ["env:FUNNEL_BACKEND_URL"],
    "save_funnel_context":  ["env:FUNNEL_BACKEND_URL"],
    "save_funnel_memory":   ["env:FUNNEL_BACKEND_URL"],
    "search_prior_runs":    ["env:FUNNEL_BACKEND_URL"],
    "list_funnels":         ["env:FUNNEL_BACKEND_URL"],
    "get_funnel_audit":     ["env:FUNNEL_BACKEND_URL"],
    "get_funnel_context":   ["env:FUNNEL_BACKEND_URL"],
    "get_pipeline_audit":   ["env:FUNNEL_BACKEND_URL"],

    # Compute services (GPU) — hidden until user configures the service URL.
    # Roadmap: v1.3.x (Modal walkthrough) unlocks docking; v1.4.x MD on Runpod;
    # v1.5.x structure prediction; v1.6.x QM + NNP block.
    "dock_molecules":         ["env:AUTODOCK_GPU_URL"],
    "dock_with_strain":       ["env:AUTODOCK_GPU_URL"],
    "run_molecular_dynamics": ["env:GROMACS_MD_URL"],
    "generate_dynamics":      ["env:GROMACS_MD_URL"],
    "predict_structure":      ["env:OPENFOLD3_URL"],
    "get_protein_structure":  ["env:OPENFOLD3_URL"],
    "get_structure_result":   ["env:OPENFOLD3_URL"],

    # QM + NNP tools — CPU-capable but need the service running.
    # Roadmap: v1.6.x.
    "run_qm_calculation":              ["env:NOVOMCP_QM_URL"],
    "run_conformer_search":            ["env:NOVOMCP_QM_URL"],
    "compute_energy":                  ["env:NOVOMCP_NNP_URL"],
    "predict_frontier_orbitals":       ["env:NOVOMCP_QM_URL"],
    "predict_pka":                     ["env:NOVOMCP_PROPERTIES_URL"],
    "predict_solubility":              ["env:NOVOMCP_PROPERTIES_URL"],
    "predict_bde":                     ["env:NOVOMCP_PROPERTIES_URL"],
    "predict_reaction_thermodynamics": ["env:NOVOMCP_QM_URL"],
    "run_qm_hessian":                  ["env:NOVOMCP_QM_URL"],
    "run_excited_states":              ["env:NOVOMCP_QM_URL"],
    "predict_redox_potential":         ["env:NOVOMCP_QM_URL"],
    "find_transition_state":           ["env:NOVOMCP_NEB_URL"],
    "optimize_geometry_nnp":           ["env:NOVOMCP_NNP_URL"],
    "parameterize_metal":              ["env:NOVOMCP_QM_URL"],

    # NovoExpert-3 weights — roadmap v1.7.x publishes MIT weights.
    "predict_clinical_outcomes": ["env:NOVOEXPERT_URL"],

    # Lead optimization + MolMIM — services required.
    "lead_optimization": ["env:LEAD_OPTIMIZATION_URL"],
    "optimize_molecule": ["env:MOLMIM_OPTIMIZER_URL"],

    # Literature via Pinecone — roadmap v1.2.x adds PubMed fallback.
    "search_literature": ["env:PINECONE_API_KEY"],
    "search_patents":    ["env:PINECONE_API_KEY"],

    # Compliance path — needs any compliance service (FAVES is one valid
    # backend; users can wire their own or use a Kaggle-hosted alternative
    # once the reference index server ships in v1.1.5).
    "check_compliance": ["env:NOVOMCP_COMPLIANCE_URL"],

    # Tree-guided retrieval — needs a molecule index. Same
    # provider-agnostic gate as similarity.
    "explore_chemical_space": ["env:NOVOMCP_MOLECULE_INDEX_URL"],
    "drill_into_cluster":     ["env:NOVOMCP_MOLECULE_INDEX_URL"],
    "vector_search":          ["env:NOVOMCP_MOLECULE_INDEX_URL"],
    "compare_candidates":     ["env:NOVOMCP_MOLECULE_INDEX_URL"],

    # ADMET prediction — needs addie-models service.
    # Roadmap: v1.3.x or bundled with a deployment guide.
    "predict_admet": ["env:ADDIE_MODELS_URL"],

    # Credit tracking + file intelligence — need the funnel-backend infra.
    "get_credit_usage":    ["env:FUNNEL_BACKEND_URL"],
    "generate_upload_url": ["env:FUNNEL_BACKEND_URL"],
    "get_file_status":     ["env:FUNNEL_BACKEND_URL"],
    "list_files":          ["env:FUNNEL_BACKEND_URL"],

    # Async job tracking — needs the backend to query.
    "list_jobs":        ["env:FUNNEL_BACKEND_URL"],
    "get_job_status":   ["env:FUNNEL_BACKEND_URL"],
    "cancel_job":       ["env:FUNNEL_BACKEND_URL"],

    # Materials Project — free but needs the user's own MP_API_KEY.
    "search_materials_project": ["env:MP_API_KEY"],

    # Molecule-index tools — call out to a molecule index service (FAVES is
    # one valid backend; Kaggle-hosted / self-hosted / user-owned all work
    # once the v1.1.5 reference index server ships). Hidden in v1 by default
    # since neither env var is set; visible the moment a user wires one.
    "search_similar":   ["env:NOVOMCP_MOLECULE_INDEX_URL"],
    "filter_molecules": ["env:NOVOMCP_MOLECULE_INDEX_URL"],

    # run_novo_ag stays visible in v1: the executor now inspects env at call
    # time and returns a setup-guide message if the compute stack isn't wired
    # (instead of a broken 11-stage protocol). That way discovery still
    # shows the flagship without producing a "here's what would run" tour.
    # No entry here → always visible per the "no entry = always available"
    # convention in is_tool_locally_available.
}


def _requirement_met(req: str) -> bool:
    """True when the requirement is satisfied.

    `env:` syntax supports pipe-OR for aliases: `env:NEW_NAME|LEGACY_NAME`
    is satisfied when either variable is set. Use this to introduce a
    generic name without breaking installs that set the legacy name.
    """
    if req == "any":
        return True
    if req.startswith("env:"):
        names = [n.strip() for n in req[4:].split("|") if n.strip()]
        return any(os.getenv(n, "").strip() for n in names)
    if req.startswith("file:"):
        return os.path.exists(os.path.expanduser(req[5:]))
    return True  # unknown format — assume met (fail-open)


def is_tool_locally_available(tool_name: str) -> bool:
    """True when all of the tool's declared requirements are met.

    Hidden tools (declared in TOOL_LOCAL_REQUIREMENTS with unmet deps) can be
    exposed via NOVOMCP_SHOW_HIDDEN_TOOLS=1 for debugging.
    """
    if os.getenv("NOVOMCP_SHOW_HIDDEN_TOOLS", "").lower() in ("1", "true", "yes"):
        return True
    reqs = TOOL_LOCAL_REQUIREMENTS.get(tool_name)
    if not reqs:
        return True  # tools without an entry are always available
    return all(_requirement_met(r) for r in reqs)


def visible_tools(is_compute_surface: bool) -> Dict[str, Any]:
    """MCP_TOOLS subset visible on the given surface (compute vs core).

    Also applies the local-availability filter — tools whose service/data
    dependencies aren't wired locally are hidden from tools/list. Override
    with NOVOMCP_SHOW_HIDDEN_TOOLS=1.
    """
    return {
        name: tool
        for name, tool in MCP_TOOLS.items()
        if is_tool_visible(name, is_compute_surface) and is_tool_locally_available(name)
    }


# =============================================================================
# PROMPT_TOOL_REQUIREMENTS — mirrors TOOL_LOCAL_REQUIREMENTS for prompts.
# =============================================================================
#
# Every prompt orchestrates a set of tools. If any required tool is not
# locally available, hide the prompt too — a prompt that references invisible
# tools is dead on arrival for the LLM.
#
# Override with NOVOMCP_SHOW_HIDDEN_PROMPTS=1 for debugging.

PROMPT_TOOL_REQUIREMENTS: Dict[str, list] = {
    # each entry is a list of tool names the prompt calls.
    "quick_check": ["check_compliance"],
    "full_analysis": ["get_molecule_profile", "check_compliance"],
    "find_alternatives": ["search_similar", "check_compliance"],
    "literature_review": ["search_literature"],
    "discovery_funnel": [
        "target_discovery", "validate_target", "search_literature",
        "search_chembl", "predict_admet", "check_compliance",
        "lead_optimization", "optimize_molecule", "dock_molecules",
        "predict_clinical_outcomes", "run_molecular_dynamics",
        "stratify_patients", "save_funnel_memory",
    ],
    "discovery_funnel_interactive": [
        "target_discovery", "validate_target", "search_literature",
        "search_chembl", "predict_admet", "check_compliance",
        "lead_optimization", "optimize_molecule", "dock_molecules",
        "predict_clinical_outcomes", "run_molecular_dynamics",
        "stratify_patients", "save_funnel_stage", "save_funnel_memory",
    ],
    "deep_characterization": [
        "get_molecule_profile", "predict_pka", "predict_solubility",
        "run_conformer_search", "run_qm_calculation", "predict_bde",
    ],
    "screen_oled_library": [
        "run_qm_calculation", "run_excited_states", "predict_frontier_orbitals",
    ],
    "screen_electrolyte_library": [
        "predict_redox_potential", "run_qm_calculation",
    ],
}


def is_prompt_locally_available(prompt_name: str) -> bool:
    """True when all tools the prompt orchestrates are locally visible.

    Override with NOVOMCP_SHOW_HIDDEN_PROMPTS=1 for debugging.
    """
    if os.getenv("NOVOMCP_SHOW_HIDDEN_PROMPTS", "").lower() in ("1", "true", "yes"):
        return True
    required = PROMPT_TOOL_REQUIREMENTS.get(prompt_name)
    if not required:
        return True
    return all(is_tool_locally_available(t) for t in required)


def visible_prompts() -> Dict[str, Any]:
    """MCP_PROMPTS subset whose orchestrated tools are all locally available."""
    return {
        name: prompt
        for name, prompt in MCP_PROMPTS.items()
        if is_prompt_locally_available(name)
    }


# =============================================================================
# Funnel-eligible tool inputSchema augmentation
# =============================================================================
#
# Every LLM that reads tools/list should see `funnel_id` as a documented
# property on every tool that participates in the 11-stage discovery funnel.
# Without this, the LLM has no schema-level reminder to mint and carry a
# per-conversation funnel_id — the rule lives only in the server `instructions`
# field and the Novo AG prompt result, both of which are easy for the LLM to
# overlook during ad-hoc conversations.
#
# Injecting once at module load (rather than at every tools/list response
# build) keeps the per-request cost zero and means both the JSON-RPC handler
# (mcp_root.py) and the REST surface (router.py) see the augmented schemas.
#
# Scope: stages of the canonical Novo AG funnel + the funnel-management tools
# that need to be threaded with the same id (save_funnel_context,
# get_funnel_context, get_funnel_audit). save_funnel_stage and
# save_funnel_memory already have funnel_id in their schemas — they stay
# untouched.

FUNNEL_ELIGIBLE_TOOLS = {
    # Pre-step
    "search_prior_runs",
    # Stage 1 — Target Discovery
    "target_discovery",
    # Stage 2 — Validation
    "validate_target",
    # Stage 3 — Literature
    "search_literature", "search_biorxiv", "search_patents",
    # Stage 4 — Known Actives
    "search_chembl",
    # Stage 5 — ADMET + Properties
    "predict_admet", "predict_pka", "predict_solubility",
    # Stage 6 — Compliance
    "check_compliance",
    # Stage 7 — Lead Optimization
    "lead_optimization", "optimize_molecule",
    # Stage 8 — Docking
    "dock_molecules", "dock_with_strain",
    # Stage 9 — Clinical Outcomes Gate
    "predict_clinical_outcomes",
    # Stage 10 — MD Simulation
    "run_molecular_dynamics", "generate_dynamics",
    # Stage 11 — Patient Stratification
    "stratify_patients",
    # Funnel-management tools (need the same id threaded through)
    "save_funnel_context", "get_funnel_context", "get_funnel_audit",
}

_FUNNEL_ID_PROPERTY = {
    "type": "string",
    "description": (
        "Conversation-scoped audit/learning identifier. **Mint once at the start "
        "of every conversation as `funnel_{topic_short}_{YYYYMMDD}_{HHMMSS}` (UTC) "
        "and pass on every subsequent tool call.** topic_short: 2-4 char "
        "abbreviation of the focus (e.g. 'aml', 'gbm', 'alz', 'mat'). NEVER "
        "reuse across conversations or topics. The server keys its audit log on "
        "this id — omitting it falls back to a user-keyed slot that cannot "
        "isolate parallel conversations from the same account. For autonomous "
        "full-funnel runs, run_novo_ag returns the canonical 11-stage protocol."
    ),
}


def _inject_funnel_id_into_schemas() -> None:
    """Mutate MCP_TOOLS in place: add funnel_id to every funnel-eligible
    tool's inputSchema.properties if not already declared.

    Idempotent. Safe to call multiple times. Runs once at module load.
    """
    for name in FUNNEL_ELIGIBLE_TOOLS:
        tool = MCP_TOOLS.get(name)
        if not tool:
            continue  # Tool not registered on this build — skip silently
        schema = tool.setdefault("inputSchema", {"type": "object", "properties": {}})
        props = schema.setdefault("properties", {})
        if "funnel_id" not in props:
            props["funnel_id"] = _FUNNEL_ID_PROPERTY


_inject_funnel_id_into_schemas()


# Closed-source tool registrations (absent in the OSS distribution). When
# ``mcp/tools_closed.py`` is on the import path, its ``register`` hook adds
# its tools + credit costs + batch config + surface classification + funnel
# eligibility to the tables above. The OSS release manifest excludes that
# file, so the import silently no-ops there.
try:
    from . import tools_closed as _tools_closed  # type: ignore

    _tools_closed.register(
        MCP_TOOLS,
        TOOL_CREDITS,
        BATCH_TOOLS,
        COMPUTE_ONLY_TOOLS,
        FUNNEL_ELIGIBLE_TOOLS,
    )
    # Surface + funnel_id schema stamping for the newly registered tools.
    for _name in _tools_closed.CLOSED_COMPUTE_ONLY_TOOLS:
        _tool = MCP_TOOLS.get(_name)
        if _tool is None:
            continue
        _tool["surface"] = "compute"
    for _name in _tools_closed.CLOSED_FUNNEL_ELIGIBLE_TOOLS:
        _tool = MCP_TOOLS.get(_name)
        if _tool is None:
            continue
        _schema = _tool.setdefault("inputSchema", {"type": "object", "properties": {}})
        _props = _schema.setdefault("properties", {})
        if "funnel_id" not in _props:
            _props["funnel_id"] = _FUNNEL_ID_PROPERTY
except ImportError:
    pass


# =============================================================================
# MCP RESOURCES - Static/dynamic data clients can read
# =============================================================================

MCP_RESOURCES = {
    "compliance_schedules": {
        "uri": "novomcp://resources/compliance_schedules",
        "name": "Controlled Substance Schedules",
        "description": "DEA Schedule I-V substances, CWC chemical weapons lists, FDA banned substances, EPA PBT chemicals, and EU REACH restricted compounds. Updated monthly.",
        "mimeType": "application/json",
        "annotations": {
            "audience": ["user"]
        }
    },
    "admet_properties": {
        "uri": "novomcp://resources/admet_properties",
        "name": "Available ADMET Predictions",
        "description": "List of 40+ ADMET property predictions available including absorption (Caco-2, P-gp), distribution (BBB, PPB, VDss), metabolism (CYP450 1A2/2C9/2C19/2D6/3A4), excretion (clearance, half-life), and toxicity (hepatotoxicity, cardiotoxicity, hERG, Ames).",
        "mimeType": "application/json",
        "annotations": {
            "audience": ["user"]
        }
    },
    "tier_features": {
        "uri": "novomcp://resources/tier_features",
        "name": "Subscription Tiers & Features",
        "description": "Features available at each subscription tier: Free Trial (all tools, 250 credits), Enterprise (all tools + data connectors, custom credits).",
        "mimeType": "application/json",
        "annotations": {
            "audience": ["user"]
        }
    },
    "database_stats": {
        "uri": "novomcp://resources/database_stats",
        "name": "Database Statistics",
        "description": "Current engine statistics: which tools are visible, which optional services are configured, and per-index counts (literature, patents, molecule index) where applicable.",
        "mimeType": "application/json",
        "annotations": {
            "audience": ["user"]
        }
    },
    "changelog": {
        "uri": "novomcp://resources/changelog",
        "name": "NovoMCP Changelog",
        "description": "Recent changes to NovoMCP tools, features, and API. Check this if tools seem missing - you may need to reconnect your MCP connection to refresh the tool list.",
        "mimeType": "application/json",
        "annotations": {
            "audience": ["user", "assistant"]
        }
    }
}

# Resource data - actual content returned when resources are read
MCP_RESOURCE_DATA = {
    "compliance_schedules": {
        "dea_schedules": {
            "schedule_i": "High abuse potential, no accepted medical use (e.g., heroin, LSD, MDMA)",
            "schedule_ii": "High abuse potential, severe dependence (e.g., fentanyl, oxycodone, methamphetamine)",
            "schedule_iii": "Moderate abuse potential (e.g., ketamine, anabolic steroids)",
            "schedule_iv": "Low abuse potential (e.g., benzodiazepines, zolpidem)",
            "schedule_v": "Lowest abuse potential (e.g., low-dose codeine preparations)"
        },
        "other_lists": {
            "cwc": "Chemical Weapons Convention scheduled chemicals",
            "fda_banned": "FDA 21 CFR banned substances",
            "epa_pbt": "EPA persistent, bioaccumulative, toxic chemicals",
            "eu_reach": "EU REACH restricted substances"
        },
        "scaffold_patterns": "24 controlled substance scaffold patterns detected",
        "whitelisted": "FDA-approved drugs automatically whitelisted"
    },
    "admet_properties": {
        "absorption": ["caco2_permeability", "pgp_substrate", "pgp_inhibitor", "bioavailability"],
        "distribution": ["bbb_permeability", "plasma_protein_binding", "vdss"],
        "metabolism": ["cyp1a2_inhibitor", "cyp2c9_inhibitor", "cyp2c19_inhibitor", "cyp2d6_inhibitor", "cyp3a4_inhibitor"],
        "excretion": ["clearance", "half_life"],
        "toxicity": ["hepatotoxicity", "cardiotoxicity_1d", "cardiotoxicity_5d", "cardiotoxicity_10d", "cardiotoxicity_30d", "herg_inhibition", "ames_mutagenicity", "skin_sensitization"],
        "nuclear_receptors": ["ahr_activation", "ar_activation", "er_activation", "ppar_gamma_activation"],
        "total_predictions": 40
    },
    "tier_features": {
        "free": {
            "credits_included": 250,
            "tools": "All tools except data connectors (push_to_destination, pull_from_source)",
            "tool_count": 25,
            "description": "30-day free trial with full platform access"
        },
        "enterprise": {
            "credits_included": "Custom",
            "tools": "All tools including data connectors",
            "tool_count": 27,
            "description": "Full platform access + data warehouse integration + custom SLAs"
        }
    },
    "database_stats": {
        "molecules": {
            "total": 122000000,
            "with_admet": 122000000,
            "with_compliance": 122000000
        },
        "literature": {
            "papers": 14398,
            "source": "curated drug discovery papers"
        },
        "patents": {
            "total": 1187,
            "source": "USPTO pharmaceutical patents"
        },
        "last_updated": "2026-01-17"
    },
    "changelog": {
        "current_version": "2.7.0",
        "last_updated": "2026-02-23T00:00:00Z",
        "total_tools": 24,
        "note": "If tools listed in recent changes are not visible in your tool list, disconnect and reconnect your MCP connection to refresh.",
        "recent_changes": [
            {
                "version": "2.7.0",
                "date": "2026-02-23",
                "changes": [
                    "Added 'pull_from_source' tool — bidirectional data pipeline (Team tier)",
                    "pull_from_source actions: preview, pull, estimate_pipeline, execute_pipeline",
                    "Pull compounds from Snowflake/Databricks, run ADMET/compliance/optimization, push enriched results back",
                    "Data connectors are Enterprise-only (row limit: 10,000)",
                    "Credit preview with confirmation token for pipeline audit (21 CFR Part 11 compliance artifact)"
                ]
            },
            {
                "version": "2.6.1",
                "date": "2026-02-16",
                "changes": [
                    "Consolidated 4 export tools into unified 'push_to_destination' tool with action parameter",
                    "push_to_destination actions: list_connections, discover_schema, preview_mapping, export",
                    "Use push_to_destination(action='export', ...) to push results to BigQuery, Google Sheets, Snowflake, etc."
                ]
            },
            {
                "version": "2.6.0",
                "date": "2026-02-15",
                "changes": [
                    "Added enterprise data export pipeline with OAuth 2.0 connector support",
                    "9 supported connectors: Snowflake, BigQuery, Google Sheets, Databricks, Salesforce, PostgreSQL, Notion, Benchling, Supabase",
                    "Removed Airtable and S3/Parquet connectors (no OAuth support)",
                    "Auto field mapping with confidence scores when exporting tool results",
                    "Token refresh handled transparently for OAuth connections"
                ]
            },
            {
                "version": "2.5.2",
                "date": "2026-01-22",
                "changes": [
                    "Added get_credit_usage tool (Free, 0 credits) - dedicated tool for checking credit balance",
                    "Added version info to get_platform_info response (version, tools_updated, tool_count)",
                    "Updated get_molecule_profile to suggest predict_admet for novel molecules",
                    "Updated predict_admet description to clarify it works on ANY molecule",
                    "Added changelog MCP resource"
                ]
            },
            {
                "version": "2.5.0",
                "date": "2026-01-21",
                "changes": [
                    "Added credit/shadow billing system",
                    "Each tool now has a credit cost (0-100 credits)",
                    "Added get_platform_info(info_type='usage') for credit balance",
                    "Added mcp_command_audit table for usage tracking"
                ]
            },
            {
                "version": "2.4.0",
                "date": "2026-01-19",
                "changes": [
                    "Added get_job_status tool - universal async job checker",
                    "predict_structure now waits up to 60s for fast completions",
                    "Improved tool descriptions for async operations"
                ]
            },
            {
                "version": "2.3.0",
                "date": "2026-01-19",
                "changes": [
                    "Added get_platform_info tool - platform information and stats",
                    "Re-indexed 2,416 USPTO patents with abstracts"
                ]
            },
            {
                "version": "2.2.0",
                "date": "2026-01-17",
                "changes": [
                    "Added MCP Resources (4): compliance_schedules, admet_properties, tier_features, database_stats",
                    "Added MCP Prompts (4): quick_check, full_analysis, find_alternatives, literature_review"
                ]
            },
            {
                "version": "2.0.0",
                "date": "2026-01-18",
                "changes": [
                    "Added calculate_properties (Pro) - RDKit property calculations",
                    "Added predict_admet (Team) - 40+ ADMET predictions via ML models",
                    "Added get_3d_properties (Team) - 32+ 3D molecular properties",
                    "Added search_literature (Pro) - Search 14K drug discovery papers",
                    "Added search_patents (Pro) - Search 2.4K USPTO patents"
                ]
            }
        ]
    }
}


# =============================================================================
# MCP PROMPTS - Pre-defined interaction templates
# =============================================================================

MCP_PROMPTS = {
    "quick_check": {
        "name": "Quick Safety Check",
        "description": "Quickly check if a molecule is a controlled substance or has safety flags. Returns compliance status and any alerts.",
        "arguments": [
            {
                "name": "smiles",
                "description": "SMILES string of the molecule to check",
                "required": True
            }
        ]
    },
    "full_analysis": {
        "name": "Complete Molecular Analysis",
        "description": "Comprehensive analysis including molecular properties, ADMET predictions, compliance status, and drug-likeness scores. Best for detailed compound evaluation.",
        "arguments": [
            {
                "name": "smiles",
                "description": "SMILES string of the molecule to analyze",
                "required": True
            }
        ]
    },
    "find_alternatives": {
        "name": "Find Safe Alternatives",
        "description": "Find structurally similar molecules that pass compliance checks. Useful for lead optimization when a compound is flagged.",
        "arguments": [
            {
                "name": "smiles",
                "description": "SMILES string of the reference molecule",
                "required": True
            },
            {
                "name": "count",
                "description": "Number of alternatives to find (default: 5)",
                "required": False
            }
        ]
    },
    "literature_review": {
        "name": "Literature Review",
        "description": "Search curated drug discovery literature for a topic. Returns relevant papers with titles, authors, and DOIs.",
        "arguments": [
            {
                "name": "topic",
                "description": "Research topic to search (e.g., 'EGFR inhibitors', 'hepatotoxicity prediction')",
                "required": True
            }
        ]
    },
    "discovery_funnel": {
        "name": "Find a Drug Candidate (Autonomous)",
        "description": "Run an autonomous end-to-end drug discovery pipeline. Does NOT pause for user input — runs all stages automatically. Use 'Find a Drug Candidate (Interactive)' instead if you want to review and approve at each stage.",
        "arguments": [
            {
                "name": "disease",
                "description": "Disease or indication to target (e.g., 'lung adenocarcinoma', 'glioblastoma', 'triple-negative breast cancer')",
                "required": True
            },
            {
                "name": "md_duration_ns",
                "description": "MD simulation duration in nanoseconds (default: 1, short ~2 min). Increase for production runs.",
                "required": False
            }
        ]
    },
    "deep_characterization": {
        "name": "Deep Molecule Characterization",
        "description": "Comprehensive characterization of a molecule using property prediction and quantum chemistry. Covers pKa, solubility, bond dissociation energies, conformer ensemble, and xTB quantum calculations. Best for late-stage lead analysis.",
        "arguments": [
            {
                "name": "smiles",
                "description": "SMILES string of the molecule to characterize",
                "required": True
            }
        ]
    },
    "discovery_funnel_interactive": {
        "name": "Find a Drug Candidate",
        "description": "Drug discovery pipeline that pauses at each stage for your review and approval. You choose which targets, molecules, and candidates to advance. Every decision is logged for reproducibility. This is the recommended way to run the funnel.",
        "arguments": [
            {
                "name": "disease",
                "description": "Disease or indication to target (e.g., 'lung adenocarcinoma', 'glioblastoma', 'triple-negative breast cancer')",
                "required": True
            },
            {
                "name": "md_duration_ns",
                "description": "MD simulation duration in nanoseconds (default: 1, short ~2 min). Increase for production runs.",
                "required": False
            }
        ]
    },
    "screen_oled_library": {
        "name": "Screen OLED Emitter Library",
        "description": "Screen a library of candidate molecules for OLED emission properties — HOMO/LUMO, singlet/triplet energies, oscillator strength, device role classification (phosphorescent / fluorescent / TADF / charge transport / host). Ranks candidates by singlet-triplet gap for TADF suitability. Materials-science workflow; no clinical pipeline.",
        "arguments": [
            {
                "name": "smiles_list",
                "description": "Comma-separated SMILES strings of candidates to screen, OR a single multi-line string of SMILES (one per line). Example: 'c1ccc2c(c1)c3ccccc3n2, c1ccc2[nH]c3ccccc3c2c1'. 1-100 candidates per screen.",
                "required": True
            },
            {
                "name": "emission_target",
                "description": "Optional target emission color or application. Accepted values: 'blue', 'green', 'red', 'deep-blue', 'tadf', 'phosphorescent', 'any'. Default: 'any'. Used to rank and filter results.",
                "required": False
            }
        ]
    },
    "screen_electrolyte_library": {
        "name": "Screen Electrolyte Stability Library",
        "description": "Screen a library of candidate molecules for electrolyte stability against a target voltage window (oxidation and reduction). Full xTB thermodynamic cycle per candidate, per-class SMARTS calibration, and stability flags vs four standard windows (standard Li-ion, high-voltage Li-ion, Na-ion, aqueous). Materials-science workflow; no clinical pipeline.",
        "arguments": [
            {
                "name": "smiles_list",
                "description": "Comma-separated SMILES strings of candidates to screen. 1-50 candidates per screen (redox potential is ~50 credits each; batching is expensive).",
                "required": True
            },
            {
                "name": "voltage_window",
                "description": "Target stability window. Accepted values: 'standard_li_ion' (0.0-4.2 V vs Li/Li+), 'high_voltage_li_ion' (0.0-4.5 V), 'na_ion' (0.0-3.8 V vs Na/Na+), 'aqueous' (-0.5 to +1.5 V vs SHE), 'custom'. Default: 'standard_li_ion'.",
                "required": False
            },
            {
                "name": "reference_electrode",
                "description": "Reference electrode to report potentials against. Accepted values: 'Li/Li+', 'Na/Na+', 'SHE', 'Ag/AgCl', 'SCE', 'Fc/Fc+'. Default inferred from voltage_window (Li/Li+ for Li-ion windows, Na/Na+ for Na-ion, SHE for aqueous).",
                "required": False
            }
        ]
    }
}

# Prompt templates - the actual messages generated when prompts are invoked
MCP_PROMPT_TEMPLATES = {
    "quick_check": {
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": "Check if this molecule is safe and compliant: {smiles}\n\nPlease check for:\n1. DEA controlled substance status\n2. FDA banned status\n3. Structural alerts (PAINS, reactive groups)\n4. Overall compliance status"
                }
            }
        ]
    },
    "full_analysis": {
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": "Perform a complete molecular analysis for: {smiles}\n\nPlease provide:\n1. Basic molecular properties (MW, LogP, TPSA, etc.)\n2. Drug-likeness assessment (QED, Lipinski violations)\n3. ADMET predictions (if available)\n4. Full compliance check\n5. Summary and recommendations"
                }
            }
        ]
    },
    "find_alternatives": {
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": "Find {count} safe alternatives to this molecule: {smiles}\n\nRequirements:\n1. Structurally similar (Tanimoto > 0.7)\n2. Not DEA controlled\n3. No PAINS alerts\n4. Good drug-likeness (QED > 0.5)\n\nFor each alternative, show the SMILES, similarity score, and key properties."
                }
            }
        ]
    },
    "literature_review": {
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": "Search the drug discovery literature for: {topic}\n\nPlease find relevant papers and provide:\n1. Paper titles and authors\n2. Brief summary of findings\n3. DOIs for further reading\n4. How this relates to drug discovery"
                }
            }
        ]
    },
    "discovery_funnel": {
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": "Run a complete drug discovery funnel for: {disease}\nMD simulation duration: {md_duration_ns} ns (default 1 if not specified)\n\n**Canonical 11-stage scheme:** Each stage has its own integer index (1-11). Halves are not used. `save_funnel_stage` takes a separate `funnel_stage` field (integer 1-11) — that is the canonical stage marker. (`stage_index` is a monotonic event counter the server auto-increments; you do NOT pass it.) There is also one pre-step (search_prior_runs) and one terminal hook (save_funnel_memory) — neither is a numbered stage.\n\n**Wall-time estimate:** This pipeline typically runs 30-45 min end-to-end. Stages 1-7 are fast (~10 min total). Stage 8 (docking) adds ~3-5 min. Stage 10 (MD simulation) takes ~7-15 min. Keep the user informed of progress.\n\nExecute the following stages autonomously. After each stage, briefly summarize findings and explain decisions before proceeding.\n\n**STAGE ZERO — GENERATE FUNNEL ID (DO THIS FIRST, BEFORE ANY TOOL CALL):**\nGenerate funnel_id = funnel_{disease_short}_{YYYYMMDD}_{HHMMSS} using the current UTC time down to seconds. Example: funnel_gbm_20260412_143022. NEVER reuse a funnel_id from a previous run. You MUST pass this funnel_id as an argument to every single tool call in this funnel, starting with target_discovery in Stage 1 — not just save_funnel_stage. Passing funnel_id to target_discovery ensures the auto-log uses the human-readable ID from the very first event; omitting it creates an orphan row under a machine-minted ID that audit queries cannot see.\n\n**IMPORTANT — ERROR HANDLING:**\nIf any stage fails (especially MD simulation or docking), do NOT say the backend is broken or needs to be resolved. Instead:\n1. Report what happened: \"MD simulation failed for this specific structure\"\n2. Try an alternative: ligand-only simulation (omit pdb_id), or try generate_dynamics for conformational exploration\n3. If alternatives also fail, skip that stage and proceed with the data you have\n4. Never tell the user to wait for a backend fix — the service is operational, specific inputs may fail\n\n**DATA TRANSPARENCY RULE:**\nIf a tool returns low-relevance or weak results, say so explicitly. Do NOT present poor data in the best possible light. Flag limitations honestly — e.g., \"The literature search returned mostly tangential results\" or \"This ligand-only MD tells us about solution-phase behavior, not binding stability.\" Credibility requires candor.\n\n**CREDIT TRACKING:**\nMaintain a running credit total. After each tool call, update the cumulative spend. Include credits_consumed in every save_funnel_stage call. At each checkpoint, report: \"Credits used so far: X\". Expected total: ~595 credits. Major costs: lead_optimization (~150), run_molecular_dynamics (~250), predict_admet (~20), dock_molecules (~30 for 4 compounds). Stages 1-6 are light (~85 credits total). Stages 7, 10 are heavy (~425 credits combined).\n\n**AUDIT LOGGING:**\nEvery tool call is auto-logged server-side as an exploration event under your funnel_id — you do NOT need to call save_funnel_stage during this autonomous run, and you must not ask the user whether to log. Just pass the funnel_id you generated in Stage Zero to every tool call and the audit trail builds itself. (The terminal save_funnel_memory at the end is still required.)\n\n---\n\n**Pre-step — Check Prior Runs (LEARN FROM HISTORY)**\nBefore diving in, call search_prior_runs with target_gene=(if known from disease) or therapeutic_area=(disease). Returns precedents from past funnels by your org. If prior runs exist, briefly note their outcomes and lessons — avoid known failure modes, reuse successful scaffolds, calibrate your threshold expectations. If no prior runs exist, skip ahead. (Pre-step, NOT a numbered stage — do not write a save_funnel_stage row for it.)\n\n**Stage 1 — Target Discovery** (funnel_stage: 1)\nUse target_discovery with the disease and min_evidence: 0.4. Pass funnel_id. Pick the top-ranked dockable target. Note the suggested_pdb_id and gene_symbol.\n\n**Stage 2 — Target Validation (ADVERSARIAL CHECKPOINT)** (funnel_stage: 2)\nCall validate_target on the gene picked in Stage 1 with the disease. Returns a 0-1 confidence score + recommendation (proceed / proceed_with_caution / reconsider) from 5 evidence streams (omics, clinical trials, literature, ChEMBL, competitive landscape) plus target_maturity classification (mature_validated / emerging / novel). If recommendation is \"reconsider\" and confidence < 0.4, go back to Stage 1 and pick a different top target — don't commit compute credits to a weakly-validated target. If \"proceed_with_caution\", note the risk factors in your summary and proceed. If \"proceed\", continue to Stage 3. Pass funnel_id.\n\n**Stage 3 — Literature** (funnel_stage: 3)\nRun 2-3 targeted literature searches with different angles:\n1. search_literature for \"[gene_symbol] [disease] inhibitors\" (broad)\n2. search_literature for \"[specific_compound] [disease]\" (compound-specific, if known)\n3. search_biorxiv for recent preprints on the target\nIf results are low-relevance, say so — do not pad weak literature.\n\n**Stage 4 — Known Actives** (funnel_stage: 4)\nSearch ChEMBL using search_type: \"activity\" for the target gene to find compounds with measured IC50/Ki. Also try search_type: \"target\" to find the ChEMBL target ID. If results don't include well-known clinical compounds for this target, note the gap and supplement with your knowledge of established inhibitors. Pick the most potent by IC50 as the seed molecule.\n\n**Stage 5 — ADMET + Properties** (funnel_stage: 5)\nRun predict_admet on the seed SMILES. If predict_pka and predict_solubility are available (Novo Compute), also run those. If not, note that detailed ionization and solubility analysis requires Novo Compute.\n\n**Stage 6 — Compliance** (funnel_stage: 6)\nRun check_compliance on the seed SMILES. If it fails, pick a different seed from Stage 4.\n\n**Stage 7 — Lead Optimization** (funnel_stage: 7)\nRun BOTH optimization approaches and compare:\n1. lead_optimization with optimization_type: \"scaffold_hop\" and num_variants: 5 for structurally diverse variants\n2. optimize_molecule (MolMIM) with num_variants: 5 for property-optimized variants close to the seed\nPresent both sets side by side. Show chemical space shift for each: delta MW, delta LogP, delta TPSA, similarity score vs seed.\n\n**Stage 8 — Docking** (funnel_stage: 8)\nIf dock_molecules is available (Novo Compute), dock the top 3 variants + seed against the suggested_pdb_id. Before calling, tell the user: \"Starting docking now. If this is the first GPU call in a while, expect ~2-3 min warm-up — target_discovery fired a background ping at Stage 1, so the GPU should already be ready.\" Pass funnel_id. dock_molecules is two-phase: report the phase=estimate cost in one line, then re-invoke with the confirmation_token in the same turn. For batch docking, poll get_job_status. For the top binder, run dock_with_strain to validate the pose. When presenting results, ALWAYS show binding affinity RELATIVE to the seed compound (delta kcal/mol). Never show absolute affinity in isolation. Flag any strain > 5 kcal/mol as potentially unrealistic. If docking is not available (e.g., connection refused), note it requires Novo Compute and proceed to Stage 11.\n\n**Stage 9 — Clinical Outcomes Gate (ECONOMIC FILTER)** (funnel_stage: 9)\nCall predict_clinical_outcomes on the top binder from Stage 8. NovoExpert v3 predicts Phase I clearance probability integrating physicochemical properties, compliance signals, and the 31-endpoint ADMET model set. Cost: 25 credits. If clearance probability is low (< 0.4), strongly consider skipping the 250-credit MD simulation in Stage 10 and picking the second-ranked binder for MD instead — this saves significant credits and focuses MD on the most developable candidate. If clearance is reasonable (> 0.5), proceed to MD with confidence. Report the decision explicitly in your summary.\n\n**Stage 10 — MD Simulation** (funnel_stage: 10)\nIf run_molecular_dynamics is available (Novo Compute), run it with the best binder (or second-ranked if Stage 9 flagged the top binder). Pass funnel_id. Also pass intent='pose_stability' (drives the v3 scientific_adequacy gate to the binding-pose rule with a sampling recommendation in the result) and adaptive_equilibration=true (replaces the fixed 100ps NPT with an adaptive loop that extends until water density plateau is detected — recommended for protein-ligand complexes where 100ps often leaves density still drifting). The GPU should already be warm from target_discovery's ping. Poll get_job_status every 60s until completed. MD takes 7-15 minutes of actual simulation time (plus cold-start delay if the GPU happens to be cold). If MD fails, try ligand-only (omit pdb_id) or generate_dynamics. If you fall back to ligand-only MD, clearly state that it shows solution-phase conformational behavior, NOT binding stability. Do NOT present RMSD convergence as evidence of binding stability. Note that the binding question remains open. If these tools are not available, note that simulation requires Novo Compute and proceed to Stage 11.\n\n**Stage 11 — Patient Stratification** (funnel_stage: 11)\nRun stratify_patients with the best binder, target_gene, disease, and ADMET results from Stage 5.\n\n**Terminal hook — Save Terminal Summary (CROSS-RUN LEARNING)**\nAfter Stage 11, call save_funnel_memory with:\n- funnel_id (the one you generated)\n- target_gene, target_pdb_id, therapeutic_area from the run\n- outcome: SUCCEEDED if you reached Stage 11, otherwise FAILED_* matching the reason\n- final_lead_count: how many lead candidates made it through\n- best_affinity_kcal: the top binding affinity from Stage 8 (negative number)\n- failure_pattern: JSON object describing what didn't work, if anything (e.g., {\"compliance_block\": true, \"reason\": \"top variants hit compliance alerts\"})\n- decisions: JSON object capturing key decisions (scaffold choices, why you picked this seed, why you deprioritized any compound)\n- summary: 2-4 sentences in natural language describing the run, its outcome, and the lesson. This powers semantic search for future funnels on similar targets.\nThis single call seeds cross-run learning for your org. Future runs on the same gene or therapeutic area will retrieve this memory via search_prior_runs. (Terminal hook, NOT a numbered stage — do not write a save_funnel_stage row for it.)\n\n**Final Summary**\nPresent the complete funnel with go/no-go signals and cumulative credit spend. Tell the user: \"The audit log is saved as {funnel_id}. Retrieve it with get_funnel_audit. Cross-run memory is saved; future funnels on {target_gene} or {therapeutic_area} will learn from this run.\""
                }
            }
        ]
    },
    "deep_characterization": {
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": "Perform a deep characterization of this molecule: {smiles}\n\nRun the following analyses and present a comprehensive report:\n\n**1. Basic Properties**\nUse get_molecule_profile to get the full profile including ADMET predictions and compliance.\n\n**2. pKa Prediction**\nIf predict_pka is available, use it to identify ionizable groups and their pKa values. Explain the implications for absorption at physiological pH (stomach pH 1-3, intestinal pH 6-7, blood pH 7.4). If not available, call novo_compute_info to let the user know this requires Novo Compute.\n\n**3. Solubility**\nIf predict_solubility is available, get LogS at 25°C. Classify as: highly soluble (>-2), soluble (-2 to -4), sparingly soluble (-4 to -6), or insoluble (<-6). If not available, note it requires Novo Compute.\n\n**4. Conformer Analysis**\nIf run_conformer_search is available, use it with max_conformers 10. This is an ASYNC JOB — it returns a job_id. Tell the user to wait ~10 minutes and check back. Do NOT auto-poll in a loop. If not available, call novo_compute_info to let the user know conformer search requires Novo Compute.\n\n**5. Quantum Properties**\nIf run_qm_calculation is available, use it with calculation_type 'energy' to get HOMO/LUMO energies, dipole moment, and electronic properties. If not available, note it requires Novo Compute.\n\n**6. Bond Dissociation Energies**\nIf predict_bde is available, use it to identify metabolic soft spots — bonds with BDE < 85 kcal/mol. If not available, note it requires Novo Compute.\n\n**Summary**\nPresent a one-page summary with:\n- Drug-likeness verdict (pass/fail with reasons)\n- Key physicochemical properties\n- Ionization profile at physiological pH (if pKa available)\n- Solubility classification (if solubility available)\n- Conformational flexibility assessment (if conformer search available)\n- Electronic properties (if QM available)\n- Metabolic soft spots (if BDE available)\n- Which Novo Compute tools would enhance this analysis (if any were unavailable)\n- Overall recommendation: advance, optimize, or reject"
                }
            }
        ]
    },
    "discovery_funnel_interactive": {
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": "Run an interactive drug discovery funnel for: {disease}\nMD simulation duration: {md_duration_ns} ns (default 1 if not specified)\n\nThis is a human-in-the-loop pipeline with a persistent audit log for reproducibility.\n\n**Canonical 11-stage scheme:** Each stage has its own integer index (1-11). Halves are not used. `save_funnel_stage` takes a separate `funnel_stage` field (integer 1-11) — that is the canonical stage marker. (`stage_index` is a monotonic event counter the server auto-increments; you do NOT pass it.) There is also one pre-step (search_prior_runs) and one terminal hook (save_funnel_memory) — neither is a numbered stage.\n\n**CRITICAL — MANDATORY STOP BEHAVIOR:**\nYou MUST stop after presenting each stage's results. Do NOT proceed to the next stage until the user explicitly responds. Each stage is a separate assistant turn — end your message after presenting results and asking for the user's decision. If you find yourself writing the next stage header, STOP IMMEDIATELY. Violating this rule makes the audit log invalid because human_decision will be missing.\n\nI may adjust parameters, remove molecules, or change direction at any stage.\n\n**DATA TRANSPARENCY RULE:**\nIf a tool returns low-relevance or weak results, say so explicitly. Do NOT present poor data in the best possible light. Flag limitations honestly. Credibility requires candor.\n\n**CREDIT TRACKING:**\nMaintain a running credit total. After each tool call, update the cumulative spend. Include credits_consumed in every save_funnel_stage call. At each checkpoint, report: \"Credits used so far: X\". Expected total: ~595 credits. Major costs: lead_optimization (~150), run_molecular_dynamics (~250), predict_admet (~20), dock_molecules (~30 for 4 compounds).\n\n**STAGE ZERO — GENERATE FUNNEL ID (DO THIS FIRST, BEFORE ANY TOOL CALL):**\nGenerate funnel_id = funnel_{disease_short}_{YYYYMMDD}_{HHMMSS} using the current UTC time down to seconds. Example: funnel_gbm_20260412_143022. NEVER reuse a funnel_id from a previous run. You MUST pass this funnel_id as an argument to every single tool call in this funnel, starting with target_discovery in Stage 1 — not just save_funnel_stage. This keeps the audit trail under one queryable ID.\n\n**Pre-step — Check Prior Runs (LEARN FROM HISTORY):**\nBefore diving into Stage 1, call search_prior_runs with therapeutic_area=(disease) or target_gene if known. If prior runs exist, briefly surface their outcomes to the user before asking about target selection. If no prior runs exist, proceed to Stage 1. (Pre-step, NOT a numbered stage — do not write a save_funnel_stage row for it.)\n\n**IMPORTANT — AUDIT LOGGING:**\nRaw tool calls are auto-logged server-side as exploration events under your funnel_id — never ask the user whether to log, and never call save_funnel_stage merely to record that a tool ran. The reason to call save_funnel_stage in THIS interactive funnel is to capture the USER'S DECISION at each checkpoint (human_decision + human_prompt) — the one thing the auto-log can't see. So: after EACH stage completes AND the user responds, call save_funnel_stage with:\n- funnel_id, funnel_stage (integer 1-11, canonical stage marker), stage_name, stage_label (do NOT pass stage_index — server auto-assigns it as a monotonic event counter)\n- tool_name: the MCP tool you called\n- tool_arguments: what you passed to the tool\n- results_summary: key findings (not full payload — just the important numbers/decisions)\n- ai_recommendation: what you suggested to the user\n- human_decision: what the user chose (after they respond)\n- human_prompt: the user's actual message (after they respond)\n- molecules_in / molecules_out: molecule counts entering/leaving this stage\n- molecules_filtered: breakdown by reason (e.g., {\"invalid_smiles\": 2, \"compliance_block\": 3})\n- curation_method: filters applied, order, thresholds (for library curation stage)\n- credits_consumed: credits used at this stage\n- context_forward: state to carry to next stage (target_gene, pdb_id, seed_smiles, etc.)\n- human_reviewed: true (ALWAYS true for interactive funnel — you only log AFTER the human responds)\n\nThis audit log is the reproducibility record. A reviewer must be able to reconstruct every decision.\n\n---\n\n**STAGE 1 — Target Discovery** (funnel_stage: 1)\n\nUse target_discovery with the disease and min_evidence: 0.4, max_targets: 10. Pass funnel_id. If the pre-step returned prior runs, mention them in your response before asking the user to choose a target.\n\nPresent results as a ranked table:\n| Rank | Gene | Composite Score | Known Drugs | Suggested PDB | Tractability |\n\nThen ask: \"Which target would you like to pursue?\"\n\n**STOP HERE. End your message now. When the user responds, call save_funnel_stage with human_reviewed: true and their selection.**\n\n---\n\n**STAGE 2 — Target Validation (ADVERSARIAL CHECKPOINT)** (funnel_stage: 2)\n\nCall validate_target on the gene selected in STAGE 1 with the disease. Returns a 0-1 confidence score + recommendation (proceed / proceed_with_caution / reconsider) from 5 evidence streams (omics, clinical trials, literature, ChEMBL, competitive landscape) plus target_maturity classification (mature_validated / emerging / novel). The dedicated viewer renders the confidence gauge, verdict card, 4-stream evidence grid, and risk/strength panels. Pass funnel_id.\n\nPresent the verdict inline (confidence, recommendation, 1-line rationale drawn from the strengths/risks) and ask: \"Proceed with this target, go back and pick a different one, or continue with caution (note the specific risks)?\"\n\n**STOP HERE. End your message now. When the user responds, log with save_funnel_stage (human_reviewed: true) and continue to STAGE 3.**\n\n---\n\n**STAGE 3 — Literature** (funnel_stage: 3)\n\nRun 2-3 targeted search_literature queries: \"[gene_symbol] [disease] inhibitors\", \"[specific_compound] [disease]\" (compound-specific, if known), plus search_biorxiv for recent preprints. If results are low-relevance, say so — do not pad weak literature.\n\nPresent the top 5 hits across sources with title, authors, year, and a 1-line takeaway each.\n\nAsk: \"Anything notable to factor into seed selection, or proceed to known actives?\"\n\n**STOP HERE. End your message now. When the user responds, log with save_funnel_stage (human_reviewed: true).**\n\n---\n\n**STAGE 4 — Known Actives** (funnel_stage: 4)\n\nSearch ChEMBL using search_type: \"activity\" for the target gene to find compounds with measured IC50/Ki. Also try search_type: \"target\" to find the ChEMBL target ID. If results miss well-known clinical compounds, note the gap.\n\nPresent top 5 ChEMBL hits:\n| Rank | SMILES (truncated) | IC50/Ki | Assay Type | Source |\n\nAsk: \"Which compound should be the seed molecule?\"\n\n**STOP HERE. End your message now. When the user responds, log with save_funnel_stage (human_reviewed: true).**\n\n---\n\n**STAGE 5 — ADMET + Properties** (funnel_stage: 5)\n\nRun predict_admet on the seed. If predict_pka and predict_solubility are available (Novo Compute), also run those.\n\nPresent: Properties, ADMET flags, CYP substrates. If pKa/solubility tools are available, include ionization profile and solubility classification. If not, note these require Novo Compute.\n\nAsk: \"Proceed to compliance check, or pick a different seed?\"\n\n**STOP HERE. End your message now. When the user responds, log with save_funnel_stage (human_reviewed: true).**\n\n---\n\n**STAGE 6 — Compliance** (funnel_stage: 6)\n\nRun check_compliance on the seed. Present the compliance outcome (pass / fail / flagged), the specific alerts, and recommended action.\n\nAsk: \"Proceed to lead optimization with this seed, swap to a different ChEMBL hit, or stop here?\"\n\n**STOP HERE. End your message now. When the user responds, log with save_funnel_stage (human_reviewed: true).**\n\n---\n\n**STAGE 7 — Lead Optimization & Library Curation** (funnel_stage: 7)\n\nThis is the CRITICAL checkpoint.\n\nRun BOTH optimization approaches and compare:\n1. lead_optimization with optimization_type: \"scaffold_hop\", num_variants: 5-10 for structurally diverse variants\n2. optimize_molecule (MolMIM) with num_variants: 5 for property-optimized variants close to the seed\nShow chemical space shift for each: delta MW, delta LogP, delta TPSA, similarity score vs seed.\n\nPresent the LIBRARY AUDIT:\n\n**Inverted Funnel:**\n- Input: N variants generated\n- Valid SMILES: N\n- Lipinski pass: N\n- ADMET screened: N (list critical flags)\n- Compliance clear: N (list blocked + why)\n- Ready for docking: N\n\n**Library Composition:** MW range, LogP range, Mean QED, Unique scaffolds\n\n**Full Molecule Table:**\n| # | SMILES | MW | LogP | QED | hERG | Compliance | Status | Reason |\nShow ALL molecules including excluded ones.\n\nAsk: \"Proceed with all N, remove specific molecules, adjust filters, or add the seed?\"\n\n**STOP. Log with save_funnel_stage including:**\n- molecules_in: total generated\n- molecules_out: user-approved count\n- molecules_filtered: {\"compliance_block\": N, \"user_removed\": N, ...}\n- curation_method: {\"filters_applied\": [...], \"thresholds\": {...}, \"library_composition\": {...}}\n\n---\n\n**STAGE 8 — Molecular Docking** (funnel_stage: 8)\n\nIf dock_molecules is available (Novo Compute), dock approved molecules + seed against the target PDB. Before calling, tell the user: \"Starting docking now. First GPU call may take ~2-3 min for warm-up — target_discovery fired a background ping earlier, so the GPU should already be ready.\" Pass funnel_id. dock_molecules is two-phase: report the phase=estimate cost in one line, then re-invoke with the confirmation_token in the same turn. Poll get_job_status for batch results.\n\nFor the top binder, also run dock_with_strain to check if the binding pose is realistic (high strain = pose may be an artifact).\n\nWhen presenting results, ALWAYS show binding affinity RELATIVE to the seed compound (delta kcal/mol). Never show absolute affinity in isolation — senior chemists consider this meaningless. Note key interactions (H-bonds, hydrophobic contacts).\n\nIf docking is not available, note it requires Novo Compute and skip to Stage 11.\n\nPresent ranked by binding affinity (include delta vs seed):\n| Rank | SMILES | Binding Affinity (kcal/mol) | Strain (kcal/mol) | Assessment |\n\nFlag any with strain > 5 kcal/mol as potentially unrealistic poses.\n\nAsk: \"Which compound(s) to advance?\"\n\n**STOP HERE. End your message now. When the user responds, log with save_funnel_stage (human_reviewed: true).**\n\n---\n\n**STAGE 9 — Clinical Outcomes Gate (ECONOMIC FILTER)** (funnel_stage: 9)\n\nCall predict_clinical_outcomes on the top binder from STAGE 8 (or the user-selected binder). NovoExpert v3 predicts Phase I clearance probability integrating physicochemical properties, compliance signals, and the 31-endpoint ADMET model set. Cost: 25 credits. The viewer renders the probability gauge with 0.4/0.6 color bands + SHAP waterfall (top 15 drivers).\n\nDecision rule:\n- Clearance probability < 0.4 → strongly consider skipping the 250-credit MD simulation in STAGE 10 and picking the second-ranked binder for MD instead. This saves credits and focuses MD on the most developable candidate.\n- Probability 0.4-0.6 → proceed to MD but note the risk.\n- Probability > 0.6 → proceed to MD with confidence.\n\n**Domain-gate note:** predict_clinical_outcomes refuses oncology prompts (the model is not validated in that domain). If the competence check refuses, proceed to STAGE 10 without the gate and note the reason.\n\nAsk: \"Proceed to MD with the top binder, switch to the second-ranked binder, or skip MD entirely?\"\n\n**STOP HERE. End your message now. When the user responds, log with save_funnel_stage (human_reviewed: true).**\n\n---\n\n**STAGE 10 — Molecular Dynamics** (funnel_stage: 10)\n\nIf run_molecular_dynamics is available (Novo Compute), run it. Pass funnel_id. Also pass intent='pose_stability' (drives v3 scientific_adequacy grading and surfaces a sampling-needed estimate in the result) and adaptive_equilibration=true (replaces the fixed 100ps NPT with a loop that extends until water density plateau is detected — recommended for protein-ligand complexes). GPU should be warm from the earlier ping. Poll get_job_status every 60 seconds until completed. MD takes 7-15 minutes of actual simulation time.\n\nWhen presenting the result, read quality_report.scientific_adequacy.pose_stability — it grades HIGH/MEDIUM/LOW/INSUFFICIENT and, when below HIGH, includes estimated_additional_sampling_ns with explicit bounds and a heuristic-not-a-guarantee note. Present that estimate alongside the trajectory analysis so the user can decide whether to extend or commit.\n\nIf run_molecular_dynamics is not available, note that MD simulation requires Novo Compute and skip to Stage 11.\n\nIf MD fails for this structure:\n1. Do NOT say the backend is broken or needs to be resolved\n2. Try alternative: ligand-only simulation (omit pdb_id)\n3. Try generate_dynamics for AI-accelerated conformational exploration\n4. If all fail, report: \"MD could not complete for this specific structure. The docking results from Stage 8 remain valid.\"\n\nIMPORTANT: If you fall back to ligand-only MD, clearly state that it shows solution-phase conformational behavior, NOT binding stability. Do NOT present RMSD convergence as evidence of binding stability. The binding question remains open.\n\nIf successful, present: RMSD convergence, equilibration stability, verdict.\n\nInclude in save_funnel_stage system_metadata:\n- force_field, water_model, temperature, pressure, duration\n- (these are from the MD results if available)\n\nAsk: \"Accept and proceed, extend simulation, or try different compound?\"\n\n**STOP HERE. End your message now. When the user responds, log with save_funnel_stage (human_reviewed: true).**\n\n---\n\n**STAGE 11 — Patient Stratification & Final Report** (funnel_stage: 11)\n\nRun stratify_patients. Present final report with go/no-go signals.\n\nCall save_funnel_stage one final time with the complete summary.\n\n**Terminal hook — Save Terminal Summary (CROSS-RUN LEARNING):**\nAfter Stage 11, call save_funnel_memory with:\n- funnel_id\n- target_gene, target_pdb_id, therapeutic_area\n- outcome: SUCCEEDED if you reached patient stratification, otherwise FAILED_* matching reason, or ABANDONED if user stopped mid-funnel\n- final_lead_count, best_affinity_kcal\n- failure_pattern (if any): JSON describing what didn't work\n- decisions: JSON capturing key user decisions made during the run\n- summary: 2-4 natural-language sentences describing the run, outcome, and lesson\nThis seeds cross-run memory. Future funnels on similar targets will retrieve this via search_prior_runs. (Terminal hook, NOT a numbered stage — do not write a save_funnel_stage row for it.)\n\nPresent:\n**Discovery Funnel Summary**\n| Stage | Input | Output | Key Decision | Decided By |\n(Fill from the audit log you've been building)\n\n**Go/No-Go Signals** and **Recommended next steps.**\n\nTell the user: \"The full audit log for this funnel is saved as {funnel_id}. You can retrieve it anytime with get_funnel_audit or view it at app.novomcp.com/audit/pipelines. Cross-run memory is saved; future funnels on {target_gene} or {therapeutic_area} will learn from this run.\""
                }
            }
        ]
    },
    "screen_oled_library": {
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": "Screen this library of OLED emitter candidates. SMILES list: {smiles_list}\nEmission target: {emission_target} (default: 'any' — rank by general OLED suitability)\n\n**Scope note:** This is a materials-science workflow, NOT a drug discovery funnel. Do NOT call target_discovery, validate_target, predict_admet, check_compliance, or any pipeline-audit/funnel-memory tool. No funnel_id, no save_funnel_stage, no save_funnel_memory. This is an ad-hoc property screen.\n\n**PHASE 1 — Geometry optimization (batch):**\nFor each SMILES, call optimize_geometry_nnp with method='auto' (routes organics to ANI-2x, unusual elements to MACE-MP-0). If a candidate has a charged species or element outside ANI-2x coverage, note it and either retry with method='mace' or fall back to xTB via run_qm_calculation with calculation_type='optimize'. Collect the relaxed xyz geometries. Runtime: ~300-500 ms per candidate.\n\n**PHASE 2 — Frontier-orbital screen:**\nFor each optimized candidate, call predict_frontier_orbitals. You get HOMO, LUMO, HOMO-LUMO gap, S1/T1 from sTDA-xTB, oscillator strength, and a device role classification (phosphorescent / fluorescent / TADF / charge transport / host / not emissive). Also returns detected OLED-relevant motifs (carbazole, triphenylamine, anthracene, pyrene, oxadiazole, triazine, Ir/Pt complexes — 14 motifs total). Cost: 20 credits each.\n\n**PHASE 3 — Excited-state deep-dive (top candidates only):**\nFor the top 5 candidates by frontier-orbital screening (filtered by device role != 'not_emissive' and oscillator strength > 0.01), call run_excited_states for a physics-based singlet/triplet analysis. Use num_states=5. Returns oscillator strengths and singlet-triplet gap — critical for TADF if that's the emission_target. Cost: 25 credits each.\n\n**PHASE 4 — Ranking:**\nRank candidates by:\n- If emission_target is 'tadf': smallest singlet-triplet gap (TADF requires ΔE_ST < 0.3 eV), high oscillator strength, carbazole or donor-acceptor motif detected.\n- If emission_target is 'blue' / 'green' / 'red' / 'deep-blue': S1 energy within the target window (blue ~2.75-3.1 eV / green 2.3-2.6 eV / red 1.8-2.1 eV / deep-blue > 2.95 eV), oscillator strength > 0.1, singlet emission role.\n- If emission_target is 'phosphorescent': T1 within target window, Ir/Pt complex motif detected, high T1 oscillator strength via spin-orbit coupling (approximate).\n- If emission_target is 'any': overall OLED suitability — high oscillator strength, clear device role, reasonable singlet-triplet separation.\n\n**PRESENT RESULTS AS A RANKED TABLE:**\n| Rank | SMILES (truncated to 40 chars) | HOMO (eV) | LUMO (eV) | Gap (eV) | S1 (eV) | T1 (eV) | f_osc | Device Role | Motifs | Verdict |\n\nBelow the table, list:\n- Top 3 candidates with a 1-line rationale each (why they rank high for the emission_target)\n- Any 'not_emissive' candidates with 1-line reason (no chromophore / saturated / too small gap)\n- Total credits spent across all 3 phases\n\nFinish with: \"Ready to deep-dive any of these? I can run excited-state analysis at a larger num_states window, compute redox potentials for electrochemical stability, or check reaction thermodynamics for a synthetic route.\""
                }
            }
        ]
    },
    "screen_electrolyte_library": {
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": "Screen this library of electrolyte candidates for stability. SMILES list: {smiles_list}\nVoltage window: {voltage_window} (default: 'standard_li_ion' = 0.0-4.2 V vs Li/Li+)\nReference electrode: {reference_electrode} (default: inferred from voltage_window)\n\n**Scope note:** This is a materials-science workflow, NOT a drug discovery funnel. Do NOT call target_discovery, predict_admet, check_compliance, or any pipeline/funnel tool. No funnel_id, no save_funnel_stage, no save_funnel_memory. This is an ad-hoc electrochemical stability screen.\n\n**Window specifications:**\n- standard_li_ion: 0.0-4.2 V vs Li/Li+ (reduction window 0.0, oxidation 4.2)\n- high_voltage_li_ion: 0.0-4.5 V vs Li/Li+\n- na_ion: 0.0-3.8 V vs Na/Na+\n- aqueous: -0.5 to +1.5 V vs SHE (thermodynamic water stability window)\n- custom: ask the user for explicit bounds before proceeding\n\n**PHASE 1 — Geometry optimization (batch):**\nFor each SMILES, call optimize_geometry_nnp with method='auto'. Neutral species only in this phase (redox calc handles charged states internally). If a candidate fails ANI-2x (unusual element), retry with 'mace' or fall back to run_qm_calculation optimize. Collect relaxed geometries.\n\n**PHASE 2 — Redox potential calc (expensive — batch cost warning):**\nFor each optimized candidate, call predict_redox_potential with reference_electrode set per the voltage window. This runs the full xTB thermodynamic cycle: neutral optimization, cation optimization, anion optimization, vertical IP, vertical EA, then converts to oxidation/reduction potentials vs the chosen reference. Per-class SMARTS calibration applies automatically (nitriles 0.003 V MAE, sulfones 0.019 V, carbonates 0.318 V). Returns oxidation_potential_v, reduction_potential_v, stability flags for 4 voltage windows, and a calibration_class field indicating which calibration ran.\n\n**COST WARNING:** 50 credits per candidate. Before Phase 2, tell the user: \"Redox screen on {N} candidates will cost {N*50} credits. This is the expensive step — proceed?\" If the user confirms or the list is ≤ 5, proceed. If > 20 candidates, recommend a 2-pass approach: first pass uses predict_frontier_orbitals (20 credits) as a cheap pre-screen to filter candidates with plausible HOMO/LUMO in the redox window, then full redox only on survivors.\n\n**PHASE 3 — Stability classification:**\nFor each candidate, classify against the requested voltage_window:\n- STABLE: oxidation_potential > window_upper AND reduction_potential < window_lower\n- OXIDATION-LIMITED: oxidation_potential < window_upper (will oxidize at top of window)\n- REDUCTION-LIMITED: reduction_potential > window_lower (will reduce at bottom of window)\n- UNSTABLE: both limits fail\n\nInclude the safety margin: how far below / above the window each candidate sits (e.g. \"oxidation at 4.8 V, 0.6 V headroom vs 4.2 V window\").\n\n**Known boundary — water:**\nIf any SMILES is 'O' (water) or similar, skip the redox call and return a note: \"Water as solute not supported — ALPB self-solvation artifacts produce unreliable values.\"\n\n**PRESENT RESULTS AS A RANKED TABLE:**\n| Rank | SMILES (truncated) | Calibration Class | E_ox (V vs ref) | E_red (V vs ref) | Window Verdict | Margin (V) |\n\nBelow the table:\n- Top 3 stable candidates with 1-line rationale (calibration class MAE, headroom)\n- Any that failed the window with the limiting potential + how far out of window\n- Total credits spent\n- If the screen suggests a specific use case (e.g. high-voltage cathode electrolyte, anode protection additive, ionic-liquid solvent), say so explicitly with reasoning\n\nFinish with: \"Ready to deep-dive? I can check reaction thermodynamics for oxidation/reduction decomposition paths, compute activation barriers via find_transition_state for transition metal side reactions, or pull analog candidates from search_materials_project.\""
                }
            }
        ]
    }
}


# =============================================================================
# TOOL EXECUTOR
# =============================================================================

@dataclass
class ToolResult:
    """Result from executing an MCP tool."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    usage: Dict[str, Any] = field(default_factory=dict)


class GpuWarmingException(BaseException):
    """Raised when a GPU HTTP service is cold-starting from replicas=0.

    Inherits from `BaseException` (not `Exception`) on purpose: tools dispatch
    via `_call_service` inside `try/except Exception` blocks for normal error
    handling (per-molecule failures in dock_molecules, generic failures in
    generate_dynamics, etc.). A regular Exception would be swallowed by those
    catches and converted into a per-molecule "infra error" or a generic
    "Dynamics generation failed" — defeating the warming envelope.

    By extending BaseException, this exception bypasses `except Exception` and
    propagates cleanly up to the central `execute()` handler at the top of
    every tool invocation, where it's converted into a `_warming_tool_result`
    envelope. Same pattern as `asyncio.CancelledError`: a control-flow signal
    that must reach the top level intact, not a domain error to be handled
    in-line.

    The class still behaves like an exception for asyncio.gather (sibling
    cancellation, propagation to the awaiter) — exactly what we want for
    parallel dock_molecules: if autodock-gpu is cold, cancel the other
    pending dock_single calls and surface one warming envelope.
    """

    def __init__(self, service: str, retry_after_s: int = 180):
        self.service = service
        self.retry_after_s = retry_after_s
        super().__init__(f"{service} is cold-starting (retry in {retry_after_s}s)")


class MCPToolExecutor:
    """
    Executes MCP tools with proper data flow:
    1. Known molecules → enriched parquet (pre-computed ADMET + FAVES)
    2. Novel molecules → FAVES context-free check
    3. Context queries → FAVES context-dependent (runtime)

    Credit tracking (v2.5):
    - Each tool has a credit cost defined in TOOL_CREDITS
    - Usage is recorded via dashboard-aggregator API
    - Credits are deducted from organization balance
    """

    def __init__(self, service_urls: Dict[str, str], internal_api_key: str):
        self.service_urls = service_urls
        self.internal_api_key = internal_api_key
        from config import settings as _settings
        # Explicit connection-pool limits. keepalive_expiry recycles idle
        # connections so we're less likely to hand out one an upstream/LB has
        # already dropped; the retry in _call_service is the real catch for the
        # stale-connection-mid-request case the pool alone can't prevent.
        self.client = httpx.AsyncClient(
            timeout=30.0,
            verify=_settings.httpx_verify,
            limits=httpx.Limits(max_keepalive_connections=32, max_connections=128, keepalive_expiry=5.0),
        )

        # Injected by `router.setup_mcp()` at startup. When None (early
        # startup or tests that instantiate the executor directly), the
        # fallback path in `_record_credit_usage` takes over.
        self.spine = None  # type: ignore[assignment]
        # Per-service API keys (fall back to internal key if not set)
        self.service_api_keys = {
            "faves-compliance": os.getenv("NOVOMCP_COMPLIANCE_API_KEY") or internal_api_key,
            "molmim-optimizer": os.getenv("MOLMIM_OPTIMIZER_API_KEY") or internal_api_key,
            "openfold3": os.getenv("OPENFOLD3_API_KEY") or internal_api_key,
            "novomd": os.getenv("NOVOMD_API_KEY") or internal_api_key,
            "chem-props": os.getenv("CHEM_PROPS_API_KEY") or internal_api_key,
            "addie-models": os.getenv("ADDIE_MODELS_API_KEY") or internal_api_key,
            "autodock-gpu": os.getenv("AUTODOCK_GPU_API_KEY") or internal_api_key,
            "novo-quantum": os.getenv("NOVO_QUANTUM_API_KEY") or internal_api_key,
            "gromacs-md": os.getenv("GROMACS_API_KEY") or internal_api_key,
            "lead-optimization": os.getenv("LEAD_OPT_API_KEY") or internal_api_key,
            "novomcp-properties": os.getenv("NOVOMCP_PROPERTIES_API_KEY") or internal_api_key,
            "novomcp-qm": os.getenv("NOVOMCP_QM_API_KEY") or internal_api_key,
            "novomcp-nnp": os.getenv("NOVOMCP_NNP_API_KEY") or internal_api_key,
            "novomcp-neb": os.getenv("NOVOMCP_NEB_API_KEY") or internal_api_key,
            "alphaflow": os.getenv("ALPHAFLOW_API_KEY") or internal_api_key,
            "novoexpert": os.getenv("NOVOEXPERT_API_KEY") or internal_api_key,
        }

        # Funnel-persistence + credit-ledger backend URL.
        # Preferred generic env var is FUNNEL_BACKEND_URL. Legacy
        # DASHBOARD_AGGREGATOR_URL kept as fallback for existing hosted
        # deploys. Users provide their own audit/credit service — the
        # engine talks to it over HTTP; no specific service required.
        self.dashboard_url = os.getenv("FUNNEL_BACKEND_URL") or os.getenv("DASHBOARD_AGGREGATOR_URL", "")
        self.dashboard_api_key = os.getenv("FUNNEL_BACKEND_API_KEY") or os.getenv("DASHBOARD_AGGREGATOR_API_KEY", "")
        self.dashboard_admin_key = os.getenv("NOVOMCP_ADMIN_KEY", os.getenv("MCP_ADMIN_KEY", ""))

        # Bridge service for connector execution
        self.bridge_url = os.getenv(
            "BRIDGE_URL",
            ""
        )
        self.bridge_key = os.getenv("BRIDGE_INTERNAL_KEY", "")

        # Long-running job control plane (MD).
        # Submits k8s Jobs on EKS (ported from Azure Container Apps Jobs 2026-06-01)
        # 2026-06-01. The attribute name is preserved (`_azure_jobs`) so
        # call sites don't shift — `_get_azure_jobs()` now returns a
        # `core.k8s_jobs.K8sJobsClient`. Same interface
        # (start_job_execution / stop_job_execution).
        self._azure_jobs = None
        # MD dispatch uses the same long-running-job pattern. Template name
        # matches the ConfigMap `<name>-job-template` in default.
        self._gromacs_md_job_name = os.getenv("GROMACS_MD_JOB_NAME", "gromacs-md-job")

        # Redis for confirmation tokens (dock, pipeline)
        self._redis: Optional[aioredis.Redis] = None
        self._redis_url = _settings.REDIS_URL
        self._redis_prefix = _settings.REDIS_KEY_PREFIX
        self._token_fallback: Dict[str, Dict[str, Any]] = {}  # in-memory fallback
        # Keyed by (user_id, surface). surface="" preserves the legacy single-slot
        # behavior for Claude (no X-Novo-Surface header); chrome-ext-v1 / word-addin-v1
        # get distinct slots so concurrent sessions don't collapse into one funnel_id.
        self._funnel_slot_fallback: Dict[tuple, tuple] = {}  # (user_id, surface) -> (funnel_id, expiry_ts)

        # File Intelligence Layer — upload once, reference everywhere
        # (PR3 2026-05-31: backed by S3 + Aurora files.mcp_files; IRSA + Aurora
        # password from Secrets Manager. Constructor args kept as no-ops for
        # backward compatibility with legacy env wiring.)
        self._file_client = None
        try:
            from core.file_intelligence import FileIntelligenceClient
            self._file_client = FileIntelligenceClient()
            logger.info("File Intelligence Layer initialized (S3 + Aurora)")
        except Exception as e:
            logger.warning(f"File Intelligence Layer init failed: {e}")

        # Tree-guided retrieval executor
        self.tree_search = TreeSearchExecutor(
            call_service_fn=self._call_service,
            lookup_enriched_fn=self._lookup_enriched,
            map_cosmos_fn=self._map_cosmos_to_mcp,
        )

        # Version info for tool suggestions
        self.platform_version = "2.7.0"
        self.tools_updated = "2026-03-19T00:00:00Z"

    def _tool_suggestion(self, tool_name: str, reason: str) -> Dict[str, Any]:
        """
        Generate a standardized tool suggestion with version info and reconnect hint.
        Used when one tool's response suggests using another tool.

        Args:
            tool_name: Name of the suggested tool
            reason: Why this tool is being suggested

        Returns:
            Structured suggestion dict with tool info, version, and reconnect hint
        """
        tool_def = MCP_TOOLS.get(tool_name)
        if not tool_def:
            return {"suggestion": f"Consider using '{tool_name}' (tool not found in registry)"}

        credits = TOOL_CREDITS.get(tool_name, 0)
        tier = tool_def["tier"].value

        return {
            "suggested_tool": {
                "name": tool_name,
                "title": tool_def.get("title", tool_name),
                "description": tool_def.get("description", ""),
                "tier": tier,
                "credits": credits
            },
            "reason": reason,
            "version_info": {
                "platform_version": self.platform_version,
                "tools_updated": self.tools_updated,
                "reconnect_hint": f"If '{tool_name}' is not visible in your tool list, disconnect and reconnect your MCP connection to refresh (tools last updated: {self.tools_updated})."
            }
        }

    async def _get_redis(self) -> Optional[aioredis.Redis]:
        """Lazy-connect to Redis. Returns None if unavailable."""
        if self._redis is not None:
            try:
                await self._redis.ping()
                return self._redis
            except Exception:
                self._redis = None
        if self._redis_url:
            try:
                self._redis = await aioredis.from_url(
                    self._redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=3,
                )
                await self._redis.ping()
                return self._redis
            except Exception as e:
                logger.warning(f"Redis unavailable for confirmation tokens, using in-memory fallback: {e}")
                self._redis = None
        return None

    async def _resolve_funnel_id(
        self,
        user_id: Optional[str],
        explicit_funnel_id: Optional[str] = None,
        surface: str = "",
    ) -> Optional[str]:
        """Resolve a stable funnel_id for every authenticated call.

        Priority:
          1. explicit_funnel_id (LLM passed it) — cached so subsequent calls inherit
          2. Redis slot for user — reuse existing, slide 30-min TTL
          3. Auto-mint fnl_u{hash}_{minute} — cache in Redis
          4. In-process fallback if Redis unreachable
        Returns None only when user_id is also None.

        `surface` namespaces the slot so a researcher running Chrome extension on
        PubChem and a Word add-in on a manuscript at the same time don't collapse
        into one funnel_id. Empty surface preserves the legacy key (Claude default).
        """
        _TTL = 3600  # 60-minute sliding window (covers human review pauses in interactive funnels)

        if not user_id:
            return explicit_funnel_id

        # Empty surface keeps the legacy redis_key shape so existing Claude sessions
        # don't get reset by this rollout. Distinct surfaces get their own slot.
        if surface:
            redis_key = f"{self._redis_prefix}:funnel_slot:{user_id}:{surface}"
        else:
            redis_key = f"{self._redis_prefix}:funnel_slot:{user_id}"
        fallback_key = (user_id, surface)

        # Tier 1: explicit override. The LLM mints a per-conversation funnel_id
        # (per the server `instructions`) and passes it on every call, so honor it
        # directly. We deliberately do NOT cache it into the shared user-slot:
        # the slot is keyed on user only (Claude shares one MCP connection across
        # all chat windows and issues no per-conversation session id, so the
        # server has no per-conversation key). Caching one conversation's id there
        # would bleed it into a DIFFERENT, interleaved conversation's id-less
        # calls — the exact cross-track contamination we're preventing. Leaving
        # explicit ids un-cached keeps parallel conversations isolated; within-
        # conversation continuity comes from the LLM carrying the id on each call.
        if explicit_funnel_id:
            return explicit_funnel_id

        # Tier 2: existing Redis slot
        r = await self._get_redis()
        if r:
            try:
                existing = await r.get(redis_key)
                if existing:
                    await r.expire(redis_key, _TTL)
                    return existing
            except Exception:
                r = None  # fall through to in-memory

        if not r:
            # In-memory fallback check
            slot = self._funnel_slot_fallback.get(fallback_key)
            if slot and slot[1] > time.time():
                self._funnel_slot_fallback[fallback_key] = (slot[0], time.time() + _TTL)
                return slot[0]
            elif slot:
                del self._funnel_slot_fallback[fallback_key]

        # Tier 3/4: mint new funnel_id
        user_hash = hashlib.sha256(user_id.encode()).hexdigest()[:12]
        minute_bucket = int(time.time() // 60)
        new_id = f"fnl_u{user_hash}_{minute_bucket}"

        if r:
            try:
                await r.set(redis_key, new_id, ex=_TTL)
            except Exception:
                pass
        else:
            self._funnel_slot_fallback[fallback_key] = (new_id, time.time() + _TTL)

        return new_id

    async def _warmup_gpu_services(self) -> None:
        """Background scale-from-zero for the GPU HTTP services the funnel will hit.

        Fired when target_discovery (the funnel entry point) is called. By the
        time the LLM reaches Stage 6 (docking) or Stage 7 (MD) ~20-40 min later
        the Deployments have replicas≥1 and the GPU node is hot via
        cluster-autoscaler.

        Skipped in OSS local mode (not running in a k8s pod) — otherwise every
        funnel entry point would emit 4 SA-token warnings that mean nothing
        outside a cluster.

        Fire-and-forget, idempotent: `kickstart_warmup` is a fast no-op if the
        Service already has ready endpoints, or if a warmup was triggered
        within the in-process TTL window.
        """
        import os as _os
        # Same detection pattern as the GPU idle reaper — no SA token means
        # we're outside the cluster and every kickstart call will fail.
        if not _os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token"):
            logger.debug("GPU warmup skipped: not running in a k8s pod (no in-cluster SA token)")
            return
        from core.k8s_scaler import get_scaler, ScalerError
        scaler = get_scaler()
        # Each GPU service the funnel may dispatch synchronously. openfold3 is
        # the structure-prediction step (target_validation), alphaflow is the
        # conformational-dynamics step. gromacs-md and autodock-gpu cover the
        # CPU + GPU work for parameterize_metal / dock_molecules.
        for service in ("autodock-gpu", "openfold3", "alphaflow", "gromacs-md"):
            try:
                await scaler.kickstart_warmup(service)
            except ScalerError as e:
                # SA token / RBAC issue — log once and move on; the direct
                # dispatch path will surface a clear "warming" envelope.
                logger.warning("warmup kickstart failed for %s: %s", service, e)
            except Exception as e:
                logger.debug("warmup kickstart unexpected for %s: %s", service, e)

    async def _store_token(self, token: str, data: Dict[str, Any], ttl: int = 600) -> None:
        """Store a confirmation token in Redis (preferred) or in-memory fallback."""
        r = await self._get_redis()
        if r:
            key = f"{self._redis_prefix}:token:{token}"
            await r.set(key, json.dumps(data), ex=ttl)
        else:
            data["_expires"] = int(time.time()) + ttl
            self._token_fallback[token] = data

    async def _get_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Retrieve and validate a confirmation token. Returns None if missing/expired/used."""
        r = await self._get_redis()
        if r:
            key = f"{self._redis_prefix}:token:{token}"
            raw = await r.get(key)
            if not raw:
                return None
            data = json.loads(raw)
            if data.get("used"):
                return None
            return data
        else:
            data = self._token_fallback.get(token)
            if not data:
                return None
            if data.get("used"):
                return None
            if int(time.time()) > data.get("_expires", 0):
                del self._token_fallback[token]
                return None
            return data

    async def _mark_token_used(self, token: str) -> None:
        """Mark a token as consumed so it cannot be replayed."""
        r = await self._get_redis()
        if r:
            key = f"{self._redis_prefix}:token:{token}"
            raw = await r.get(key)
            if raw:
                data = json.loads(raw)
                data["used"] = True
                ttl = await r.ttl(key)
                await r.set(key, json.dumps(data), ex=max(ttl, 60))
        else:
            data = self._token_fallback.get(token)
            if data:
                data["used"] = True

    async def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        user_tier: ToolTier,
        org_id: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_email: Optional[str] = None,
        credits_available: Optional[float] = None,
        surface: str = "",
        client_tag: str = "",
    ) -> ToolResult:
        """
        Execute an MCP tool with credit tracking.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            user_tier: User's subscription tier
            org_id: Organization ID for credit tracking
            user_id: User ID (key_id) for audit
            session_id: MCP session ID for correlation
        """
        import time
        import hashlib

        start_time = time.time()

        if tool_name not in MCP_TOOLS:
            return ToolResult(success=False, error=f"Unknown tool: {tool_name}")

        tool_def = MCP_TOOLS[tool_name]
        required_tier = tool_def["tier"]

        # Normalize legacy tiers for comparison
        normalized_user = normalize_tier(user_tier.value if isinstance(user_tier, ToolTier) else user_tier)
        normalized_required = normalize_tier(required_tier.value if isinstance(required_tier, ToolTier) else required_tier)

        tier_order = [ToolTier.FREE, ToolTier.CORE, ToolTier.PRO, ToolTier.TEAM, ToolTier.ENTERPRISE]
        user_tier_enum = ToolTier(normalized_user)
        required_tier_enum = ToolTier(normalized_required)
        if tier_order.index(user_tier_enum) < tier_order.index(required_tier_enum):
            return ToolResult(
                success=False,
                error=f"Tool '{tool_name}' requires {required_tier.value} tier or higher"
            )

        handler = getattr(self, f"_execute_{tool_name}", None)
        if handler is None:
            return ToolResult(success=False, error=f"Tool '{tool_name}' not implemented")

        # Get credit cost for this tool
        credit_cost = TOOL_CREDITS.get(tool_name, 1)

        # Pre-flight credit check: batch-aware estimate before running the handler.
        # Uses cached MCPUser.credits_available (passed from router). Bounded staleness
        # (~5 min max, refreshed on every tool call). The authoritative atomic check
        # is _record_credit_usage post-execution — this is the optimization layer.
        if credits_available is not None and credit_cost > 0:
            estimated_cost = credit_cost
            batch_param = BATCH_TOOLS.get(tool_name)
            if batch_param:
                batch_size = len(arguments.get(batch_param) or [])
                if batch_size > 0:
                    estimated_cost = credit_cost * batch_size
            if credits_available < estimated_cost:
                return ToolResult(
                    success=False,
                    error=(
                        f"Insufficient credits: {credits_available:.0f} available, "
                        f"{estimated_cost} needed for {tool_name}"
                        f"{f' × {len(arguments.get(batch_param) or [])}' if batch_param and batch_size > 1 else ''}. "
                        f"Purchase more at novomcp.com/pricing."
                    ),
                    usage={"tool": tool_name, "credits_needed": estimated_cost, "credits_available": credits_available},
                )

        # Resolve a stable funnel_id for every authenticated call via user_id +
        # Redis. First call mints, subsequent calls within 30 min inherit.
        # `surface` namespaces the slot per client (chrome-ext-v1, word-addin-v1, ...).
        effective_funnel_id = await self._resolve_funnel_id(
            user_id=user_id,
            explicit_funnel_id=arguments.get("funnel_id"),
            surface=surface,
        )
        if effective_funnel_id and "funnel_id" not in arguments:
            arguments = {**arguments, "funnel_id": effective_funnel_id}

        # Build context for handlers that need user/org info
        context = {
            "org_id": org_id,
            "user_id": user_id,
            "user_email": user_email,
            "session_id": session_id,
            "funnel_id": effective_funnel_id,
            "user_tier": user_tier.value,
            # surface tag (chrome-ext-v1, word-addin-v1, "" for Claude) so handlers
            # — particularly _execute_save_funnel_stage — can persist it into the
            # audit row, enabling per-surface analytics on funnel_audit_log.
            "surface": surface,
            # client tag — finer-grained identifier (claude-code, cursor, hex.tech-mcp,
            # NovoMCP-WordAddin/1.2.3, ...). Persisted to system_metadata.client.
            "client": client_tag,
        }

        try:
            # Pass context to handlers that accept it
            import inspect
            sig = inspect.signature(handler)
            if "context" in sig.parameters:
                result = await handler(arguments, context=context)
            else:
                result = await handler(arguments)
            execution_time_ms = int((time.time() - start_time) * 1000)

            # Respect executor-provided dynamic credits (e.g. per-molecule docking)
            effective_credits = result.usage.pop("_dynamic_credits", None)
            if effective_credits is not None:
                credit_cost = effective_credits

            # Add credit info to result
            result.usage["credits"] = credit_cost
            result.usage["tool"] = tool_name
            # Surface the resolved funnel_id so clients (Chrome extension,
            # NovoWorkbench, dashboard) can deep-link to the audit trail and
            # hand off cross-surface continuity. Without this the audit row
            # exists server-side but no caller can address it.
            if effective_funnel_id:
                result.usage["funnel_id"] = effective_funnel_id

            # Record usage if org_id provided (async, don't block on failure)
            # Only charge credits for successful tool executions
            #
            # Visibility: surface WHY a tool call doesn't write to
            # mcp_command_audit. Before this log existed, the silent skip on
            # missing org_id caused 18 days of frozen dashboards (May 19 →
            # Jun 6) — no warning anywhere because each guard term has a
            # legitimate reason to be False. Logging at INFO so a single
            # `kubectl logs | grep credit_skip` answers "why no audit row?"
            if not (org_id and credit_cost > 0 and result.success):
                logger.info(
                    f"credit_skip tool={tool_name} "
                    f"org_id={'set' if org_id else 'NONE'} "
                    f"credit_cost={credit_cost} "
                    f"success={result.success}"
                )
            if org_id and credit_cost > 0 and result.success:
                try:
                    # Hash arguments for analytics (don't store raw data)
                    args_hash = hashlib.sha256(str(arguments).encode()).hexdigest()[:64]

                    credit_result = await self._record_credit_usage(
                        org_id=org_id,
                        user_id=user_id or "unknown",
                        tool_name=tool_name,
                        tool_tier=required_tier.value,
                        credit_cost=credit_cost,
                        arguments_hash=args_hash,
                        execution_time_ms=execution_time_ms,
                        success=result.success,
                        error_message=result.error[:500] if result.error else None,
                        session_id=session_id,
                        surface=surface,
                    )

                    if credit_result:
                        credits_remaining = credit_result.get("credits_remaining")
                        result.usage["credits_remaining"] = credits_remaining
                        result.usage["credit_status"] = credit_result.get("credit_status", "ok")

                        # Low balance warning when credits < 50
                        if credits_remaining is not None and credits_remaining < 50:
                            result.usage["credit_warning"] = {
                                "credits_remaining": credits_remaining,
                                "message": f"Low balance: {credits_remaining:.0f} credits remaining.",
                                "upgrade_url": "https://novomcp.com/pricing",
                            }

                except Exception as e:
                    # Don't fail the tool execution if credit tracking fails
                    logger.warning(f"Credit tracking failed for {tool_name}: {e}")

            # Re-read funnel_id after handler returns so that handlers which
            # upgrade the Redis slot (e.g. future set_session_mode) are reflected.
            # Pass the explicit id from arguments (set pre-handler) so the LLM's
            # per-conversation funnel_id is preserved — it is no longer cached in
            # the shared user-slot, so an explicit-less re-resolve would drop it
            # and mis-file the auto-log under the user-slot id.
            if user_id:
                effective_funnel_id = await self._resolve_funnel_id(
                    user_id=user_id,
                    explicit_funnel_id=arguments.get("funnel_id"),
                    surface=surface,
                )

            # Emit a tool_call audit event to the configured sink. Runs on
            # every call (success or failure) so the audit trail is complete.
            # Local runs get JSON-lines in ~/.novo/audit.jsonl; custom sinks
            # can route wherever they want.
            if self.spine is not None:
                try:
                    from .spine import User as _SpineUser
                    _audit_user = _SpineUser(
                        user_id=user_id or "unknown",
                        org_id=org_id or "",
                    )
                    await self.spine.audit.emit(
                        "tool_call",
                        {
                            "tool": tool_name,
                            "funnel_id": effective_funnel_id,
                            "success": result.success,
                            "credits": credit_cost,
                            "execution_time_ms": execution_time_ms,
                            "surface": surface,
                            "error": (result.error or "")[:200] if not result.success else None,
                        },
                        user=_audit_user,
                    )
                except Exception as e:
                    logger.debug(f"Audit emit failed: {e}")

            if effective_funnel_id and tool_name not in FUNNEL_AUTOLOG_SKIP:
                try:
                    event_payload = _sanitize_for_json({
                        "funnel_id": effective_funnel_id,
                        "event_type": "exploration",
                        "tool_name": tool_name,
                        "stage_name": tool_name,
                        "tool_arguments": _redact_arguments(arguments),
                        "results_summary": _summarize_result_data(result.data) if result.success else None,
                        "credits_consumed": credit_cost,
                        "execution_time_ms": execution_time_ms,
                        "human_reviewed": False,
                        "surface": surface,
                        "client": client_tag,
                    })
                    if not result.success:
                        event_payload["results_summary"] = {"error": (result.error or "")[:500]}
                    context["funnel_id"] = effective_funnel_id
                    asyncio.create_task(self._autolog_event(event_payload, context))
                except Exception as e:
                    logger.debug(f"Auto-log scheduling failed for {tool_name}: {e}")

            return result

        except GpuWarmingException as gwe:
            # The GPU service this tool dispatched to was at replicas=0. The
            # scale-from-zero patch was already kicked off inside _call_service;
            # return a structured envelope telling the LLM to retry in ~3 min.
            # No credits are charged on a warming envelope.
            logger.info("tool %s returned warming envelope (service=%s)", tool_name, gwe.service)
            return self._warming_tool_result(tool_name, gwe.service, gwe.retry_after_s)

        except Exception as e:
            logger.exception(f"Error executing tool {tool_name}")
            return ToolResult(success=False, error=str(e), usage={"credits": credit_cost, "tool": tool_name})

    async def _autolog_event(self, payload: Dict[str, Any], context: Dict[str, Any]) -> None:
        """Background task: log a funnel exploration event.

        Semaphore(3) caps concurrent calls — dashboard-aggregator has a single
        shared pymssql connection; burst cap prevents auth-query starvation.
        """
        try:
            async with _AUTOLOG_SEMAPHORE:
                result = await self._execute_save_funnel_stage(payload, context=context)
                if result and not result.success and result.error:
                    logger.debug(f"Auto-log rejected: {result.error}")
        except Exception as e:
            logger.debug(f"Auto-log event failed: {e}")

    async def _record_credit_usage(
        self,
        org_id: str,
        user_id: str,
        tool_name: str,
        tool_tier: str,
        credit_cost: float,
        arguments_hash: Optional[str] = None,
        execution_time_ms: Optional[int] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        session_id: Optional[str] = None,
        surface: str = "",
    ) -> Optional[Dict[str, Any]]:
        """
        Record tool usage and deduct credits.

        Routes through the injected CreditMeter when the spine is set
        (the standard path once `setup_mcp` has run). Returns a status
        dict on success or None if metering failed — callers treat None
        as "don't surface credit info."
        """
        if self.spine is not None:
            from .spine import User as _SpineUser

            meter_user = _SpineUser(
                user_id=user_id,
                tier=str(tool_tier),
                org_id=org_id,
            )
            result = await self.spine.meter.record(
                user=meter_user,
                tool=tool_name,
                cost_credits=int(credit_cost),
                meta={
                    "tool_tier": tool_tier,
                    "arguments_hash": arguments_hash,
                    "execution_time_ms": execution_time_ms,
                    "success": success,
                    "error_message": error_message,
                    "session_id": session_id,
                    "surface": surface,
                },
            )
            if not result.success:
                return None
            return {
                "credits_remaining": result.remaining_credits,
                "credit_status": result.reason or "ok",
            }

        # Fallback path (used only before setup_mcp has run, or by tests
        # that instantiate the executor directly).
        try:
            response = await self.client.post(
                f"{self.dashboard_url}/mcp/record-usage",
                json={
                    "org_id": org_id,
                    "user_id": user_id,
                    "tool_name": tool_name,
                    "tool_tier": tool_tier,
                    "credit_cost": credit_cost,
                    "arguments_hash": arguments_hash,
                    "execution_time_ms": execution_time_ms,
                    "success": success,
                    "error_message": error_message,
                    "session_id": session_id,
                    "surface": surface,
                },
                headers={"X-API-Key": self.dashboard_api_key},
                timeout=5.0
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.warning(f"Credit recording returned {response.status_code}: {response.text[:200]}")
                return None

        except Exception as e:
            logger.warning(f"Credit recording request failed: {e}")
            return None

    async def _get_org_usage(self, org_id: str) -> Optional[Dict[str, Any]]:
        """
        Get organization credit usage and balance from dashboard-aggregator.

        Returns shadow billing statement with:
        - credits_issued: Total credits allocated
        - credits_used: Total credits consumed
        - credits_remaining: Current balance
        - value_realized: Dollar value of usage (shadow)
        - usage_by_tool: Breakdown by tool
        - usage_by_day: Recent daily usage
        """
        # Local mode: dashboard-aggregator unwired → skip the fetch entirely.
        # The 'Usage fetch failed: URL missing http://' log noise was pointless.
        if not self.dashboard_url:
            logger.debug("Usage fetch skipped: no credit-ledger backend configured (local mode)")
            return None

        try:
            response = await self.client.get(
                f"{self.dashboard_url}/mcp/org/{org_id}/usage",
                headers={"X-API-Key": self.dashboard_api_key},
                timeout=10.0
            )

            if response.status_code == 200:
                data = response.json()

                # Format as shadow billing statement
                credits_used = data.get("credits_used_total", 0)
                credits_available = data.get("credits_available", 0)
                max_credits = data.get("max_credits", 1000)

                return {
                    "org_id": org_id,
                    "org_name": data.get("org_name", "Unknown"),

                    # Credit balance
                    "credits_issued": max_credits,
                    "credits_used": credits_used,
                    "credits_remaining": credits_available,

                    # Shadow value (1 credit = $0.01, displayed as $1.00)
                    "value_realized": f"${credits_used:.2f}",
                    "value_remaining": f"${credits_available:.2f}",

                    # Usage status
                    "usage_percent": round((credits_used / max_credits * 100) if max_credits > 0 else 0, 1),
                    "status": data.get("credit_status", "ok"),

                    # Breakdown
                    "usage_this_month": data.get("credits_used_this_month", 0),
                    "tool_calls_this_month": data.get("tool_calls_this_month", 0),
                    "usage_by_tool": data.get("usage_by_tool", []),

                    # Message for Claude to communicate
                    "summary": f"You have realized ${credits_used:.2f} in research value. "
                               f"Remaining balance: ${credits_available:.2f} ({round(credits_available/max_credits*100) if max_credits > 0 else 0}% of allocation)."
                }
            else:
                logger.warning(f"Usage fetch returned {response.status_code}")
                return None

        except Exception as e:
            logger.warning(f"Usage fetch failed: {e}")
            return None

    # =========================================================================
    # Helper Methods
    # =========================================================================

    # Services that use "API-Key" header instead of "X-API-Key"
    _API_KEY_HEADER_SERVICES = {"gromacs-md", "gromacs-processor", "novo-quantum", "aws-braket-quantum", "alphaflow"}

    # ── GPU scale-from-zero envelope ────────────────────────────────────────
    # The four GPU HTTP services (autodock-gpu, openfold3, alphaflow,
    # gromacs-md) default to replicas=0 for cost. cluster-autoscaler scales
    # the GPU nodegroup once a Pending pod exists, but at replicas=0 no pod
    # is created — the chicken-and-egg. `_call_service` detects this case
    # automatically: when a dispatch to a GPU service hits ConnectError, it
    # kicks off a background scale-to-1 via core.k8s_scaler and raises
    # GpuWarmingException. Every tool that dispatches to a GPU service can
    # catch this exception at its top level and return `_warming_tool_result`
    # so the LLM knows to retry in ~3 min. No credits are charged.
    # Subsequent calls within the warm window (~10 min) hit the live pod
    # immediately. After 10 min idle, cluster-autoscaler scales the node down.
    GPU_HTTP_SERVICES = frozenset({"autodock-gpu", "openfold3", "alphaflow", "gromacs-md"})
    GPU_COLD_START_SECONDS = 180  # ~3 min — GPU node provisioning + image pull + container start
    # Idle reaper: scale a GPU HTTP service back to replicas=0 after this many
    # seconds with no calls. Completes the scale-from-zero cycle (warmup does
    # 0→1; the reaper does 1→0). Without it, a service stays warm forever once
    # woken and pins a GPU. 10 min ≈ the documented warm window.
    GPU_IDLE_REAP_SECONDS = int(os.getenv("GPU_IDLE_REAP_SECONDS", "600"))

    async def _call_service(self, service: str, endpoint: str, data: Dict[str, Any],
                           method: str = "POST", timeout: float = 30.0,
                           api_key: Optional[str] = None) -> httpx.Response:
        """Call an internal service.

        For GPU services (see `GPU_HTTP_SERVICES`) that are at replicas=0,
        raises `GpuWarmingException` on connect failure after firing a
        background scale-to-1 patch. Tool dispatchers should catch the
        exception and return `_warming_tool_result(...)`.

        If the service's URL env var is unset (common in OSS local mode where
        the downstream compute isn't wired), returns a synthetic 503 Response
        with a clear message rather than crashing with httpx.UnsupportedProtocol.
        Downstream tool dispatchers already handle non-200 responses gracefully.
        """
        base = self.service_urls.get(service, "")
        if not base:
            # Service not wired — return synthetic 503, don't crash with
            # httpx.UnsupportedProtocol on an empty-scheme URL.
            import json as _json
            return httpx.Response(
                status_code=503,
                content=_json.dumps({
                    "error": f"service '{service}' not configured",
                    "hint": f"set {service.upper().replace('-', '_')}_URL to enable this tool, or deploy the service (see docs/deploying-services/).",
                }).encode(),
                headers={"content-type": "application/json"},
                request=httpx.Request(method, f"unwired://{service}{endpoint}"),
            )
        url = f"{base}{endpoint}"
        # Use explicit api_key > per-service key > internal key
        key = api_key or self.service_api_keys.get(service) or self.internal_api_key
        header_name = "API-Key" if service in self._API_KEY_HEADER_SERVICES else "X-API-Key"
        headers = {header_name: key}

        # Retry transient connection/timeout/5xx failures on a fresh connection.
        # These calls are idempotent (a prediction is a pure function of its
        # inputs) and a connection-class error means the request likely never
        # reached the service, so a retry cannot double-execute. A dropped
        # keepalive connection (RemoteProtocolError) is the common flake — the
        # "first call fails, re-run works" pattern this guards against. GPU
        # cold-start (ConnectError on a replicas=0 GPU service) keeps its own
        # warming path and is NOT tight-retried.
        _RETRIABLE_STATUS = (502, 503, 504)
        _MAX_ATTEMPTS = 3
        last_exc: Optional[Exception] = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                if method == "GET":
                    resp = await self.client.get(url, headers=headers, timeout=timeout)
                else:
                    resp = await self.client.post(url, json=data, headers=headers, timeout=timeout)
                # Stamp last-use so the idle reaper keeps a service warm while it's
                # in active use and only scales it to 0 after GPU_IDLE_REAP_SECONDS.
                if service in self.GPU_HTTP_SERVICES:
                    self._stamp_gpu_activity(service)
                # Transient upstream 5xx → retry on a fresh attempt (until the last).
                if resp.status_code in _RETRIABLE_STATUS and attempt < _MAX_ATTEMPTS - 1:
                    logger.warning("service %s%s → HTTP %s, retrying (%d/%d)",
                                   service, endpoint, resp.status_code, attempt + 1, _MAX_ATTEMPTS)
                    last_exc = None
                else:
                    return resp
            except httpx.ConnectError as e:
                # GPU HTTP service at replicas=0: this is a cold start, not an
                # outage. Stamp activity, fire a background scale-up, and surface
                # the warming envelope so the client retries after cold start.
                if service in self.GPU_HTTP_SERVICES:
                    self._stamp_gpu_activity(service)
                    from core.k8s_scaler import get_scaler
                    try:
                        scaler = get_scaler()
                        # Fire-and-forget — don't block the dispatch on the scale patch.
                        asyncio.create_task(scaler.kickstart_warmup(service))
                    except Exception as warmup_err:
                        logger.warning("scaler kickstart failed for %s: %s", service, warmup_err)
                    raise GpuWarmingException(service=service, retry_after_s=self.GPU_COLD_START_SECONDS) from e
                # CPU service: a transient connect blip → retry on a fresh connection.
                last_exc = e
            except (httpx.RemoteProtocolError, httpx.ReadTimeout, httpx.ConnectTimeout, httpx.PoolTimeout) as e:
                # Stale keepalive dropped mid-request, or a slow first response —
                # retry on a fresh connection (all services, including warm GPU).
                last_exc = e
            if attempt < _MAX_ATTEMPTS - 1:
                await asyncio.sleep(0.3 * (3 ** attempt))
        if last_exc is not None:
            raise last_exc
        # Unreachable: a 2xx/4xx/final-5xx response returns inside the loop.
        raise RuntimeError(f"_call_service exhausted for {service}{endpoint}")

    def _warming_tool_result(
        self,
        tool_name: str,
        service: str,
        retry_after_s: int = GPU_COLD_START_SECONDS,
    ) -> "ToolResult":
        """Standard 'GPU is warming, retry shortly' envelope for synchronous GPU tools."""
        return ToolResult(
            success=True,
            data={
                "status": "warming",
                "phase": "warming",
                "service": service,
                "retry_after_seconds": retry_after_s,
                "message": (
                    f"{service} is cold-starting (GPU node provisioning + container start, "
                    f"~{retry_after_s // 60} min). Re-call `{tool_name}` with the same inputs "
                    f"in ~{retry_after_s // 60} minutes — your request will execute and return "
                    "results then. No credits were charged."
                ),
                "hint": (
                    "For chained tool flows, call `target_discovery` early in the funnel — it "
                    "pre-warms the GPU services so by the time the docking/structure/MD step is "
                    "reached, the pod is already hot."
                ),
            },
            # No credits charged on a warming envelope — the work didn't happen.
            usage={"queries": 0, "tool": tool_name, "_dynamic_credits": 0},
        )

    def _stamp_gpu_activity(self, service: str) -> None:
        """Record last-use of a GPU HTTP service (Redis), best-effort + non-blocking.
        The idle reaper reads this to decide when to scale the service to 0."""
        async def _do():
            try:
                r = await self._get_redis()
                if r:
                    await r.set(f"novomcp:gpu:last-active:{service}",
                                str(int(time.time())), ex=24 * 3600)
            except Exception:
                pass
        try:
            asyncio.create_task(_do())
        except Exception:
            pass

    async def start_gpu_idle_reaper(self):
        """Background loop: scale idle GPU HTTP services back to replicas=0.

        The OTHER half of scale-from-zero. `_call_service` scales 0→1 on demand
        (kickstart_warmup) and stamps last-active; this loop scales 1→0 once a
        service has been idle for GPU_IDLE_REAP_SECONDS. cluster-autoscaler then
        drains the now-empty GPU node. Without this, a service woken once stays
        at replicas=1 indefinitely, pinning a GPU (the pre-2026-06-24 state where
        alphaflow/autodock-gpu/gromacs-md held all 3 GPUs warm).

        Safety: scale_to_zero only patches 1→0 (no-op on 0 or N>1, so a manual
        Deployments, so reaping the HTTP Deployment can't kill a running job. A
        request arriving just after a reap hits the normal warming path and the
        pod re-wakes. Per-replica lock so only one reaper acts at a time."""
        import asyncio as _asyncio
        import os as _os
        import random as _random
        from core.k8s_scaler import get_scaler
        # In-cluster detection: the reaper's whole job is to patch a k8s
        # Deployment via the in-cluster SA token. Not-in-cluster means every
        # iteration will fail with "SA token not found" and spam the log.
        # Detect at startup and skip scheduling instead of firing forever.
        if not _os.path.exists("/var/run/secrets/kubernetes.io/serviceaccount/token"):
            logger.info("GPU idle reaper: skipped (not running in a k8s pod; "
                        "no in-cluster SA token). Set GPU_IDLE_REAPER_FORCE=1 to override.")
            if _os.getenv("GPU_IDLE_REAPER_FORCE") != "1":
                return
        idle_s = max(120, self.GPU_IDLE_REAP_SECONDS)
        logger.info("GPU idle reaper: starting (idle_timeout=%ss, services=%s)",
                    idle_s, sorted(self.GPU_HTTP_SERVICES))
        while True:
            try:
                await _asyncio.sleep(150 + _random.uniform(0, 30))
                r = await self._get_redis()
                if r:
                    try:
                        if not await r.set("novomcp:gpu:reaper-lock", "1", nx=True, ex=120):
                            continue  # another replica is reaping this cycle
                    except Exception:
                        pass
                scaler = get_scaler()
                now = int(time.time())
                reaped = 0
                for svc in sorted(self.GPU_HTTP_SERVICES):
                    try:
                        last = None
                        if r:
                            raw = await r.get(f"novomcp:gpu:last-active:{svc}")
                            if raw:
                                last = int(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
                        # Recently used → keep warm. Missing key (never used since
                        # restart / TTL'd) → eligible; scale_to_zero no-ops unless
                        # it's actually at replicas==1, so this safely catches a
                        # warm-but-unused service.
                        if last is not None and (now - last) < idle_s:
                            continue
                        if await scaler.scale_to_zero(svc):
                            reaped += 1
                            logger.info("GPU idle reaper: scaled %s to 0 (idle=%ss)",
                                        svc, (now - last) if last is not None else "unknown")
                    except Exception as e:
                        logger.warning("GPU idle reaper: %s check failed: %s", svc, e)
                if r:
                    try:
                        await r.delete("novomcp:gpu:reaper-lock")
                    except Exception:
                        pass
            except _asyncio.CancelledError:
                logger.info("GPU idle reaper: cancelled — shutting down")
                break
            except Exception as e:
                logger.error("GPU idle reaper error (non-fatal): %s", e)
                await _asyncio.sleep(60)

    async def _lookup_enriched(self, smiles: str) -> Optional[Dict[str, Any]]:
        """Lookup molecule in enriched database. Returns None if not found."""
        try:
            response = await self._call_service(
                "faves-compliance", "/api/lookup", {"smiles": smiles}
            )
            if response.status_code == 200:
                data = response.json()
                if data.get("found"):
                    raw = data.get("molecule", {})
                    # Map flat Cosmos fields to nested structure expected by MCP tools
                    return self._map_cosmos_to_mcp(raw)
        except Exception as e:
            logger.warning(f"Enriched lookup failed: {e}")
        return None

    def _map_cosmos_to_mcp(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """
        Map flat Cosmos DB document to nested MCP response structure.

        Cosmos pubchem_enriched fields (from parquet):
        - Identity: id, cid, smiles
        - Properties: molecular_weight, xlogp, tpsa, qed, drug_likeness, etc.
        - Toxicity: overall_toxicity_score
        - Compliance: is_dea_controlled, is_fda_banned, is_cwc_scheduled, faves_flag_count
        - Structural: has_pains, pains_count, has_reactive_groups, has_structural_alerts
        """
        def to_bool(val) -> bool:
            """Convert string/bool to actual boolean."""
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.lower() in ('true', '1', 'yes')
            return bool(val)

        def to_float(val):
            """Convert string to float if possible."""
            if val is None:
                return None
            try:
                return float(val)
            except (ValueError, TypeError):
                return val

        def to_int(val):
            """Convert string to int if possible."""
            if val is None:
                return None
            try:
                return int(float(val))
            except (ValueError, TypeError):
                return val

        # Basic properties (use actual Cosmos field names, convert types).
        # Several fields ship under different names in the parquet vs the
        # client-facing schema — accept both so enriched-DB and computed
        # paths render consistently. parquet uses `rotatable_bonds` (not
        # `_count`), `xlogp` (not `logp`), and the H-bond counts ship as
        # either `hbd_count`/`hba_count` (current loader) or
        # `h_bond_donor_count`/`h_bond_acceptor_count` (older snapshots).
        properties = {
            "cid": to_int(raw.get("cid")),
            "smiles": raw.get("smiles"),
            "molecular_weight": to_float(raw.get("molecular_weight")),
            "molecular_formula": raw.get("molecular_formula"),
            "logp": to_float(raw.get("xlogp") if raw.get("xlogp") is not None else raw.get("logp")),
            "tpsa": to_float(raw.get("tpsa")),
            "complexity": to_float(raw.get("complexity")),
            "hbd_count": to_int(raw.get("hbd_count") if raw.get("hbd_count") is not None else (raw.get("h_bond_donor_count") if raw.get("h_bond_donor_count") is not None else raw.get("hbd"))),
            "hba_count": to_int(raw.get("hba_count") if raw.get("hba_count") is not None else (raw.get("h_bond_acceptor_count") if raw.get("h_bond_acceptor_count") is not None else raw.get("hba"))),
            "rotatable_bond_count": to_int(raw.get("rotatable_bonds") if raw.get("rotatable_bonds") is not None else raw.get("rotatable_bond_count")),
            "aromatic_ring_count": to_int(raw.get("aromatic_ring_count") if raw.get("aromatic_ring_count") is not None else raw.get("aromatic_rings")),
            "aromatic_atom_count": to_int(raw.get("aromatic_atom_count")),
            "heavy_atom_count": to_int(raw.get("heavy_atom_count")),
            "fsp3": to_float(raw.get("fsp3")),
            "qed": to_float(raw.get("qed")),
            "drug_likeness": to_float(raw.get("drug_likeness")),
            "synthetic_accessibility": to_float(raw.get("synthetic_accessibility")),
        }

        # ADMET — data.molecules carries the full pre-computed TDC ADMET set
        # (~99% coverage), not just overall_toxicity_score. Surface it in the
        # same shape predict_admet returns for novel molecules, so known and
        # novel molecules look identical to callers (toxicity incl. hepatotoxicity,
        # metabolism, nuclear receptors, stress response, absorption/distribution/
        # excretion). Missing/NULL fields are stripped below.
        def _drop_none(d):
            return {k: v for k, v in d.items() if v is not None}

        toxicity = _drop_none({
            "hepatotoxicity": to_float(raw.get("hepatotoxicity_probability")),
            "herg_inhibition": to_float(raw.get("herg_blocker_probability")),
            "cardiotoxicity_dict": to_float(raw.get("cardiotoxicity_dict_probability")),
            "ames_mutagenicity": to_float(raw.get("ames_mutagenicity_probability")),
            "carcinogenicity": to_float(raw.get("carcinogenicity_probability")),
            "clinical_toxicity": to_float(raw.get("clinical_toxicity_probability")),
            "developmental_toxicity": to_float(raw.get("developmental_toxicity_probability")),
            "reproductive_toxicity": to_float(raw.get("reproductive_toxicity_probability")),
            "respiratory_toxicity": to_float(raw.get("respiratory_toxicity_probability")),
            "eye_corrosion": to_float(raw.get("eye_corrosion_probability")),
            "eye_irritation": to_float(raw.get("eye_irritation_probability")),
            "cardiotoxicity_1d": to_float(raw.get("cardiotoxicity_1d_probability")),
            "cardiotoxicity_5d": to_float(raw.get("cardiotoxicity_5d_probability")),
            "cardiotoxicity_10d": to_float(raw.get("cardiotoxicity_10d_probability")),
            "cardiotoxicity_30d": to_float(raw.get("cardiotoxicity_30d_probability")),
            "cardiotoxicity_max": to_float(raw.get("cardiotoxicity_max_probability")),
            "ld50_log_mol_kg": to_float(raw.get("ld50_log_mol_kg")),
            "overall_toxicity_score": to_float(raw.get("overall_toxicity_score")),
        })
        metabolism = _drop_none({
            "cyp1a2_inhibitor": to_float(raw.get("cyp1a2_inhibitor_probability")),
            "cyp2c9_inhibitor": to_float(raw.get("cyp2c9_inhibitor_probability")),
            "cyp2c19_inhibitor": to_float(raw.get("cyp2c19_inhibitor_probability")),
            "cyp2d6_inhibitor": to_float(raw.get("cyp2d6_inhibitor_probability")),
            "cyp3a4_inhibitor": to_float(raw.get("cyp3a4_inhibitor_probability")),
            "cyp2c9_substrate": to_float(raw.get("cyp2c9_substrate_probability")),
            "cyp2d6_substrate": to_float(raw.get("cyp2d6_substrate_probability")),
            "cyp3a4_substrate": to_float(raw.get("cyp3a4_substrate_probability")),
            "cyp_inhibition_risk_score": to_float(raw.get("cyp_inhibition_risk_score")),
            "cyp_substrate_max_probability": to_float(raw.get("cyp_substrate_max_probability")),
        })
        nuclear_receptors = _drop_none({
            "ahr_agonist": to_float(raw.get("nr_ahr_agonist_probability")),
            "ar_agonist": to_float(raw.get("nr_ar_agonist_probability")),
            "ar_lbd_agonist": to_float(raw.get("nr_ar_lbd_agonist_probability")),
            "aromatase_inhibitor": to_float(raw.get("nr_aromatase_inhibitor_probability")),
            "er_agonist": to_float(raw.get("nr_er_agonist_probability")),
            "er_lbd_agonist": to_float(raw.get("nr_er_lbd_agonist_probability")),
            "ppar_gamma_agonist": to_float(raw.get("nr_ppar_gamma_agonist_probability")),
        })
        stress_response = _drop_none({
            "are_activation": to_float(raw.get("sr_are_activation_probability")),
            "atad5_activation": to_float(raw.get("sr_atad5_activation_probability")),
            "hse_activation": to_float(raw.get("sr_hse_activation_probability")),
            "mmp_activation": to_float(raw.get("sr_mmp_activation_probability")),
            "p53_activation": to_float(raw.get("sr_p53_activation_probability")),
        })
        absorption = _drop_none({
            "caco2_permeability": to_float(raw.get("caco2_permeability")),
            "hia_probability": to_float(raw.get("hia_probability")),
            "bioavailability": to_float(raw.get("bioavailability_probability")),
            "pgp_inhibitor": to_float(raw.get("pgp_inhibitor_probability")),
            "pgp_substrate": to_float(raw.get("pgp_substrate_probability")),
            "lipophilicity_log_ratio": to_float(raw.get("lipophilicity_log_ratio")),
            "aqueous_solubility_log_mol_l": to_float(raw.get("aqueous_solubility_log_mol_l")),
        })
        distribution = _drop_none({
            "bbb_penetration": to_float(raw.get("bbb_penetration_probability")),
            "ppbr_percent": to_float(raw.get("ppbr_percent")),
            "vdss_l_kg": to_float(raw.get("vdss_l_kg")),
        })
        excretion = _drop_none({
            "half_life_hr": to_float(raw.get("half_life_hr")),
            "clearance_hepatocyte": to_float(raw.get("clearance_hepatocyte")),
            "clearance_microsome": to_float(raw.get("clearance_microsome")),
        })

        admet = {
            "overall_toxicity_score": to_float(raw.get("overall_toxicity_score")),
            "is_aggregator_risk": to_bool(raw.get("is_aggregator_risk")),
            **({"toxicity": toxicity} if toxicity else {}),
            **({"metabolism": metabolism} if metabolism else {}),
            **({"nuclear_receptors": nuclear_receptors} if nuclear_receptors else {}),
            **({"stress_response": stress_response} if stress_response else {}),
            **({"absorption": absorption} if absorption else {}),
            **({"distribution": distribution} if distribution else {}),
            **({"excretion": excretion} if excretion else {}),
            "source": "enriched_database",
        }
        _prune_unvalidated_admet(admet)
        # Phase-1 bridge: drop the retrained-but-corpus-stale heads here; callers
        # overlay them live for known molecules (see _overlay_bridge_live).
        _strip_bridge_from_corpus(admet)

        # FAVES compliance flags (convert strings to bools)
        is_controlled = to_bool(raw.get("is_dea_controlled"))
        is_banned = to_bool(raw.get("is_fda_banned"))
        is_cwc = to_bool(raw.get("is_cwc_scheduled"))
        is_scaffold_match = to_bool(raw.get("is_scaffold_match"))
        is_whitelisted = to_bool(raw.get("is_whitelisted"))
        faves_flag_count = to_int(raw.get("faves_flag_count")) or 0

        if is_whitelisted:
            status = "whitelisted"
        elif is_controlled or is_banned or is_cwc:
            status = "controlled"
        elif is_scaffold_match or faves_flag_count > 0:
            status = "flagged"
        else:
            status = "clean"

        compliance = {
            "status": status,
            "is_dea_controlled": is_controlled,
            "is_fda_banned": is_banned,
            "is_cwc_scheduled": is_cwc,
            "is_epa_pbt": to_bool(raw.get("is_epa_pbt")),
            "is_eu_reach_banned": to_bool(raw.get("is_eu_reach_banned")),
            "is_scaffold_match": is_scaffold_match,
            "is_whitelisted": is_whitelisted,
            "faves_flag_count": faves_flag_count,
        }

        # Structural alerts
        structural_alerts = {
            "has_pains": to_bool(raw.get("has_pains")),
            "pains_count": to_int(raw.get("pains_count")) or 0,
            "has_reactive_groups": to_bool(raw.get("has_reactive_groups")),
            "reactive_group_count": to_int(raw.get("reactive_group_count")) or 0,
            "has_structural_alerts": to_bool(raw.get("has_structural_alerts")),
            "structural_alert_count": to_int(raw.get("structural_alert_count")) or 0,
        }

        # Remove None values for cleaner output
        properties = {k: v for k, v in properties.items() if v is not None}
        admet = {k: v for k, v in admet.items() if v is not None}

        return {
            "properties": properties,
            "admet": admet if admet else None,
            "compliance": compliance,
            "structural_alerts": structural_alerts,
        }

    async def _faves_context_free(self, smiles: str) -> Dict[str, Any]:
        """Run FAVES context-free check for novel molecules.

        Normalizes the remote /api/classify response (which has flat fields)
        into the same {compliance: {...}} shape used by _local_faves_check,
        so callers can consume either source interchangeably.
        """
        try:
            response = await self._call_service(
                "faves-compliance", "/api/classify", {"smiles": smiles}
            )
            if response.status_code == 200:
                raw = response.json()
                # Normalize flat /api/classify response to {compliance: {...}}
                is_controlled = bool(raw.get("is_controlled"))
                is_whitelisted = bool(raw.get("is_whitelisted"))
                faves_flag_count = len(raw.get("faves_flags") or []) if isinstance(raw.get("faves_flags"), list) else 0
                schedule = raw.get("faves_schedule") or ""
                status = (
                    "whitelisted" if is_whitelisted
                    else "controlled" if is_controlled
                    else "flagged" if faves_flag_count > 0
                    else "clean"
                )
                return {
                    "smiles": smiles,
                    "source": "faves_context_free",
                    "compliance": {
                        "status": status,
                        "is_controlled": is_controlled,
                        "is_whitelisted": is_whitelisted,
                        "dea_schedule": schedule,
                        "scaffold_category": raw.get("faves_category") or "",
                        "flags": raw.get("faves_flags") or [],
                        "flag_count": faves_flag_count,
                        "match_type": raw.get("faves_match_type"),
                        "whitelist_name": (raw.get("faves_v3") or {}).get("whitelist_name", ""),
                    },
                    # Preserve the raw response for callers that need the full ClassifyResponse
                    "raw": raw,
                }
        except Exception as e:
            logger.warning(f"FAVES context-free check failed: {e}")

        # Local fallback
        return await self._local_faves_check(smiles)

    async def _local_faves_check(self, smiles: str) -> Dict[str, Any]:
        """Local FAVES context-free check using faves_checker_v3."""
        try:
            import os
            from faves_checker_v3 import FAVESCheckerV3
            # Load reference file for direct matching of known controlled substances
            ref_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "faves_reference_lists.json")
            if os.path.exists(ref_file):
                checker = FAVESCheckerV3(ref_file)
            else:
                checker = FAVESCheckerV3()
            result = checker.check_molecule(smiles)

            return {
                "smiles": smiles,
                "source": "faves_context_free",
                "compliance": {
                    "is_controlled": result.is_dea_controlled,
                    "dea_schedule": result.dea_schedule,
                    "is_whitelisted": result.is_whitelisted,
                    "whitelist_name": result.whitelist_name,
                    "is_scaffold_match": result.is_scaffold_match,
                    "scaffold_category": result.scaffold_category,
                    "flags": result.faves_flags or [],
                    "flag_count": result.faves_flag_count,
                    "status": "whitelisted" if result.is_whitelisted else (
                        "controlled" if result.is_dea_controlled else (
                            "flagged" if result.faves_flag_count > 0 or result.is_scaffold_match else "clean"
                        )
                    )
                }
            }
        except Exception as e:
            return {"smiles": smiles, "error": str(e), "source": "faves_context_free"}

    async def _compute_basic_properties(self, smiles: str) -> Dict[str, Any]:
        """Compute basic molecular properties in-process via RDKit.

        No network dependency — works on any install where RDKit is
        importable (which is true for the default pip install). Used both
        as the fallback when chem-props is unreachable and as the
        first-choice source when no downstream service is configured.
        """
        try:
            from rdkit import Chem
            from rdkit.Chem import Descriptors, QED, Lipinski, Crippen, rdMolDescriptors
        except ImportError:
            return {"error": "RDKit not installed — pip install rdkit"}

        try:
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                return {"error": f"Invalid SMILES: {smiles}"}

            mw = Descriptors.MolWt(mol)
            logp = Crippen.MolLogP(mol)
            tpsa = Descriptors.TPSA(mol)
            hbd = Lipinski.NumHDonors(mol)
            hba = Lipinski.NumHAcceptors(mol)
            rot_bonds = Lipinski.NumRotatableBonds(mol)
            aromatic_rings = rdMolDescriptors.CalcNumAromaticRings(mol)
            heavy_atoms = mol.GetNumHeavyAtoms()

            # Lipinski Rule-of-Five violations
            lipinski_violations = sum([
                mw > 500,
                logp > 5,
                hbd > 5,
                hba > 10,
            ])

            try:
                qed_score = QED.qed(mol)
            except Exception:
                qed_score = None

            return {
                "molecular_weight": round(mw, 3),
                "exact_mass": round(Descriptors.ExactMolWt(mol), 4),
                "logp": round(logp, 3),
                "tpsa": round(tpsa, 2),
                "hbd": hbd,
                "hba": hba,
                "rotatable_bonds": rot_bonds,
                "aromatic_rings": aromatic_rings,
                "heavy_atoms": heavy_atoms,
                "qed": round(qed_score, 3) if qed_score is not None else None,
                "lipinski_violations": lipinski_violations,
                "lipinski_pass": lipinski_violations == 0,
            }
        except Exception as e:
            logger.warning(f"In-process RDKit property computation failed: {e}")
            return {"error": str(e)}

    # =========================================================================
    # Free Tier Tools
    # =========================================================================

    async def _execute_get_molecule_profile(self, args: Dict[str, Any]) -> ToolResult:
        """
        Get complete molecular profile:
        - Known molecule: Return pre-computed ADMET + FAVES from enriched DB
        - Novel molecule: Compute basic props + run FAVES context-free
        """
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        # include_admet gates the on-the-fly ADMET (addie-models) call for novel
        # molecules — the real cost/latency driver. Default True so single-call
        # semantics match the tool name; callers wanting fast properties-only can
        # opt out. (Known molecules return their pre-computed ADMET for free.)
        include_admet = args.get("include_admet", True)

        try:
            # Try enriched database first (pre-computed data)
            enriched_data = await self._lookup_enriched(smiles)

            if enriched_data:
                compliance = enriched_data.get("compliance", {}) or {}

                # Backfill missing dea_schedule for controlled molecules:
                # older enrichment runs may not have populated this field, so
                # run the live FAVES v3 checker to get the schedule when missing.
                if compliance.get("status") == "controlled" and not compliance.get("dea_schedule"):
                    try:
                        live_faves = await self._faves_context_free(smiles)
                        live_compliance = live_faves.get("compliance", {})
                        if live_compliance.get("dea_schedule"):
                            compliance["dea_schedule"] = live_compliance["dea_schedule"]
                        if not compliance.get("scaffold_category") and live_compliance.get("scaffold_category"):
                            compliance["scaffold_category"] = live_compliance["scaffold_category"]
                    except Exception:
                        pass

                admet_block = enriched_data.get("admet", {})
                # Phase-1 bridge: overlay the retrained heads LIVE for known molecules
                # (corpus is stale; _strip_bridge_from_corpus removed them). Direct calls
                # only — batch_profile/screen_library do their own batched overlay
                # (they call here with include_admet=False).
                if include_admet:
                    try:
                        _live = await self._execute_predict_admet({"smiles": smiles})
                        if _live.success and _live.data:
                            _overlay_bridge_live(admet_block, _live.data)
                    except Exception as _e:
                        logger.warning(f"bridge overlay failed for {smiles[:40]}: {_e}")
                return ToolResult(
                    success=True,
                    data={
                        "smiles": smiles,
                        "source": "enriched_database",
                        "in_database": True,
                        "properties": enriched_data.get("properties", {}),
                        "admet": admet_block,
                        "compliance": compliance,
                        "structural_alerts": enriched_data.get("structural_alerts", {})
                    },
                    # Precomputed Cosmos point-read: no on-the-fly compute, so the
                    # ambient-presence Chrome extension hover-card surface stays free.
                    # Novel-SMILES branch below still charges 1 credit (real RDKit+FAVES+ADMET).
                    usage={"queries": 1, "tool": "get_molecule_profile", "source": "enriched", "_dynamic_credits": 0}
                )

            # Novel molecule - compute basic props + FAVES (+ ADMET) in parallel
            import asyncio
            props_task = self._compute_basic_properties(smiles)
            faves_task = self._faves_context_free(smiles)

            if include_admet:
                admet_result = None
                props, faves, admet_result = await asyncio.gather(
                    props_task, faves_task, self._execute_predict_admet({"smiles": smiles})
                )
            else:
                props, faves = await asyncio.gather(props_task, faves_task)
                admet_result = None

            # Extract ADMET data from predict_admet result (exclude raw/duplicate fields to keep response compact)
            admet_data = None
            admet_available = False
            if admet_result is not None and admet_result.success and admet_result.data:
                admet_data = {
                    k: v for k, v in admet_result.data.items()
                    if k not in ("smiles", "source", "raw_predictions", "properties")
                }
                admet_available = True

            return ToolResult(
                success=True,
                data={
                    "smiles": smiles,
                    "source": "computed+admet" if include_admet else "computed",
                    "in_database": False,
                    "properties": props,
                    "compliance": faves.get("compliance", {}),
                    "admet": admet_data,
                    "admet_available": admet_available,
                },
                usage={"queries": 2 if include_admet else 1, "tool": "get_molecule_profile",
                       "source": "computed+admet" if include_admet else "computed"}
            )

        except Exception as e:
            logger.exception(f"Error in get_molecule_profile: {e}")
            return ToolResult(success=False, error=f"Molecule profile failed: {str(e)}")

    async def _execute_get_molecule_info(self, args: Dict[str, Any]) -> ToolResult:
        """Get basic molecular properties (RDKit computation)."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        props = await self._compute_basic_properties(smiles)

        if "error" in props:
            return ToolResult(success=False, error=props["error"])

        # chem-props' SA is unreliable (flat 1.0); override with a real RDKit
        # sascorer value so the seed/profile show true synthesizability. This is
        # the seed source for the lead-comparison viewer (enrichSeedProperties).
        _sa = _compute_sa_score(smiles)
        if _sa is not None:
            props["synthetic_accessibility"] = _sa
            props["sa_score"] = _sa

        return ToolResult(
            success=True,
            data={
                "smiles": smiles,
                **props,
                "tool_suggestion": self._tool_suggestion(
                    "get_molecule_profile",
                    "Get comprehensive profile including ADMET predictions (40+ properties) and compliance status for known molecules"
                )
            },
            usage={"queries": 1, "tool": "get_molecule_info"}
        )

    async def _execute_get_protein_structure(self, args: Dict[str, Any]) -> ToolResult:
        """
        Smart protein structure resolver:
        1. Try to resolve target to known PDB ID (EGFR → 1M17)
        2. For RCSB: Return proxy URL immediately (no full fetch needed)
        3. Fall back to OpenFold3 prediction if not found and sequence provided
        """
        from utils.pdb_cache import (
            get_or_predict_structure,
            extract_pdb_from_description,
            PROTEIN_PDB_MAPPING
        )

        target = args.get("target")
        sequence = args.get("sequence")
        include_ligands = args.get("include_ligands", True)

        if not target:
            return ToolResult(success=False, error="Missing required parameter: target")

        # Validate PDB ID if the target looks like one (4 chars, starts with digit)
        target_stripped = target.strip()
        if len(target_stripped) == 4 and target_stripped[0].isdigit():
            from core.validators import validate_pdb_id
            pdb_val = await validate_pdb_id(target_stripped)
            if not pdb_val.valid:
                return ToolResult(
                    success=False,
                    error=pdb_val.message or f"PDB ID '{target_stripped}' not found.",
                )

        try:
            target_upper = target.strip().upper()

            # Check if target looks like a sequence (long string of amino acids)
            amino_acids = set("ACDEFGHIKLMNPQRSTVWY")
            if len(target) > 20 and all(c in amino_acids for c in target_upper):
                sequence = target
                target = f"SEQ_{len(target)}"
                # Fall through to prediction path below

            # FAST PATH: Known protein name → PDB ID → return URL immediately
            elif target_upper in PROTEIN_PDB_MAPPING:
                pdb_id = PROTEIN_PDB_MAPPING[target_upper]
                logger.info(f"Fast path: {target} → {pdb_id}")

                # Fetch metadata + backbone-only structure
                metadata = await self._fetch_rcsb_metadata(pdb_id)
                backbone_pdb = await self._fetch_rcsb_backbone(pdb_id)

                # If backbone is large (>100KB), reduce to CA-only for inline rendering
                pdb_url = f"https://ai.novomcp.com/api/pdb/{pdb_id}"
                if backbone_pdb and len(backbone_pdb) > 100_000:
                    ca_lines = [l for l in backbone_pdb.split('\n')
                                if l.startswith(('HEADER', 'TITLE', 'END'))
                                or (l.startswith('ATOM') and l[12:16].strip() == 'CA')
                                or (l.startswith('HETATM') and l[17:20].strip() != 'HOH')]
                    backbone_pdb = '\n'.join(ca_lines)

                msg = "Backbone structure from RCSB PDB (CA atoms + non-water ligands)" if backbone_pdb else "Metadata retrieved but structure download unavailable"
                return ToolResult(
                    success=True,
                    data={
                        "target": target,
                        "pdb_id": pdb_id,
                        "source": "rcsb",
                        "pdb_data": backbone_pdb,
                        "pdb_url": pdb_url,
                        "name": metadata.get("title", f"{target} ({pdb_id})"),
                        "resolution": metadata.get("resolution"),
                        "method": metadata.get("method"),
                        "organism": metadata.get("organism"),
                        "chains": metadata.get("chains", []),
                        "ligands": metadata.get("ligands", []) if include_ligands else [],
                        "message": msg,
                    },
                    usage={"queries": 1, "tool": "get_protein_structure", "source": "rcsb"}
                )

            # FAST PATH: Valid PDB ID format (4 chars alphanumeric)
            elif len(target_upper) == 4 and target_upper.isalnum():
                pdb_id = target_upper
                logger.info(f"Fast path: PDB ID {pdb_id}")

                # Verify it exists and get metadata + backbone
                metadata = await self._fetch_rcsb_metadata(pdb_id)
                if metadata.get("exists", False):
                    backbone_pdb = await self._fetch_rcsb_backbone(pdb_id)
                    pdb_url = f"https://ai.novomcp.com/api/pdb/{pdb_id}"

                    # If backbone is large (>100KB), reduce to CA-only for inline rendering
                    # Claude's sandbox blocks fetch(), so inline data is required for the viewer
                    if backbone_pdb and len(backbone_pdb) > 100_000:
                        ca_lines = [l for l in backbone_pdb.split('\n')
                                    if l.startswith(('HEADER', 'TITLE', 'END'))
                                    or (l.startswith('ATOM') and l[12:16].strip() == 'CA')
                                    or (l.startswith('HETATM') and l[17:20].strip() != 'HOH')]
                        backbone_pdb = '\n'.join(ca_lines)

                    msg = "Backbone structure from RCSB PDB (CA atoms + non-water ligands)" if backbone_pdb else "Metadata retrieved but structure download unavailable"
                    return ToolResult(
                        success=True,
                        data={
                            "target": target,
                            "pdb_id": pdb_id,
                            "source": "rcsb",
                            "pdb_data": backbone_pdb,
                            "pdb_url": pdb_url,
                            "name": metadata.get("title", pdb_id),
                            "resolution": metadata.get("resolution"),
                            "method": metadata.get("method"),
                            "organism": metadata.get("organism"),
                            "chains": metadata.get("chains", []),
                            "ligands": metadata.get("ligands", []) if include_ligands else [],
                            "message": msg,
                        },
                        usage={"queries": 1, "tool": "get_protein_structure", "source": "rcsb"}
                    )
                # PDB ID doesn't exist, fall through to prediction

            # Try UniProt lookup if target looks like a UniProt ID
            if not sequence and len(target) >= 6 and target[0] in "PQOA" and target[1:].replace("_", "").isalnum():
                uniprot_seq = await self._fetch_uniprot_sequence(target)
                if uniprot_seq:
                    sequence = uniprot_seq
                    logger.info(f"Fetched sequence from UniProt for {target}: {len(sequence)} residues")

            # SLOW PATH: Need to predict with OpenFold3
            if sequence:
                pdb_content, source = await get_or_predict_structure(
                    target=target,
                    sequence=sequence,
                    prefer_experimental=False  # Already tried RCSB above
                )

                metadata = self._parse_pdb_metadata(pdb_content)

                return ToolResult(
                    success=True,
                    data={
                        "target": target,
                        "source": source,
                        "pdb_data": pdb_content,  # Inline for predictions
                        "name": metadata.get("title", target),
                        "sequence_length": len(sequence),
                        "message": f"Structure predicted using OpenFold3"
                    },
                    usage={"queries": 1, "tool": "get_protein_structure", "source": source}
                )

            # No valid path found
            known_proteins = ", ".join(sorted(PROTEIN_PDB_MAPPING.keys())[:10])
            return ToolResult(
                success=False,
                error=f"Could not resolve '{target}' to a structure. "
                      f"If you have a gene symbol (e.g. ERBB2, KRAS), use target_discovery or search_chembl first to find the PDB ID, then pass the PDB ID here. "
                      f"Accepted inputs: 1. A PDB ID (e.g. '1M17', '3PP0') 2. A known protein name: {known_proteins} 3. An amino acid sequence for de novo prediction.",
                usage={"queries": 0, "tool": "get_protein_structure"}
            )

        except Exception as e:
            error_msg = str(e)
            known_proteins = ", ".join(sorted(PROTEIN_PDB_MAPPING.keys())[:15])
            return ToolResult(
                success=False,
                error=f"{error_msg}. If you have a gene symbol, use target_discovery or search_chembl to find the PDB ID first. Accepted: PDB IDs, known proteins ({known_proteins}), or amino acid sequences.",
                usage={"queries": 0, "tool": "get_protein_structure"}
            )

    async def _fetch_rcsb_metadata(self, pdb_id: str) -> Dict[str, Any]:
        """Fetch just metadata from RCSB (fast, no full PDB download)."""
        try:
            url = f"https://data.rcsb.org/rest/v1/core/entry/{pdb_id}"
            response = await self.client.get(url, timeout=5.0)

            if response.status_code == 404:
                return {"exists": False}

            if response.status_code == 200:
                data = response.json()
                # Extract chain IDs from entity_poly or container identifiers
                chains = []
                for ep in data.get("entity_poly", []):
                    strand_ids = ep.get("pdbx_strand_id", "")
                    if strand_ids:
                        chains.extend(strand_ids.split(","))
                if not chains:
                    container = data.get("rcsb_entry_container_identifiers", {})
                    chains = container.get("polymer_entity_ids", [])

                # Extract ligand IDs from nonpolymer entities
                ligands = []
                for ne in data.get("rcsb_nonpolymer_entity", []) or data.get("nonpolymer_entities", []):
                    comp_id = ne.get("comp_id") or ne.get("pdbx_description", "")
                    if comp_id:
                        ligands.append(comp_id)
                if not ligands:
                    container = data.get("rcsb_entry_container_identifiers", {})
                    ligands = container.get("non_polymer_entity_ids", [])

                return {
                    "exists": True,
                    "title": data.get("struct", {}).get("title", ""),
                    "resolution": data.get("rcsb_entry_info", {}).get("resolution_combined", [None])[0],
                    "method": data.get("exptl", [{}])[0].get("method", ""),
                    "organism": data.get("rcsb_entry_info", {}).get("organism_scientific_name", [None])[0] if data.get("rcsb_entry_info") else None,
                    "chains": chains,
                    "ligands": ligands,
                }
            return {"exists": False}
        except Exception as e:
            logger.warning(f"RCSB metadata fetch failed for {pdb_id}: {e}")
            return {"exists": True, "title": pdb_id}  # Assume exists, minimal metadata

    async def _fetch_rcsb_backbone(self, pdb_id: str) -> str:
        """Fetch backbone-only PDB from RCSB (smaller, ~10x reduction).
        Falls back to mmCIF conversion for newer structures without .pdb files."""
        try:
            url = f"https://files.rcsb.org/download/{pdb_id}.pdb"
            response = await self.client.get(url, timeout=30.0)

            pdb_text = None
            if response.status_code == 200:
                pdb_text = response.text
            else:
                # Fallback: fetch mmCIF and convert to PDB-like format
                cif_url = f"https://files.rcsb.org/download/{pdb_id}.cif"
                cif_resp = await self.client.get(cif_url, timeout=30.0)
                if cif_resp.status_code == 200:
                    pdb_text = self._cif_to_pdb_backbone(cif_resp.text)

            if not pdb_text:
                return ""

            # Filter to backbone atoms (CA, C, N, O) + non-water ligands (HETATM)
            lines = []
            for line in pdb_text.split('\n'):
                if line.startswith('ATOM'):
                    atom_name = line[12:16].strip()
                    if atom_name in ('CA', 'C', 'N', 'O'):
                        lines.append(line)
                elif line.startswith('HETATM'):
                    # Skip water molecules — they dominate file size
                    res_name = line[17:20].strip()
                    if res_name != 'HOH':
                        lines.append(line)
                elif line.startswith(('HEADER', 'TITLE', 'COMPND', 'SOURCE', 'CONECT', 'END')):
                    lines.append(line)

            return '\n'.join(lines)
        except Exception as e:
            logger.warning(f"RCSB backbone fetch failed for {pdb_id}: {e}")
            return ""

    def _cif_to_pdb_backbone(self, cif_text: str) -> str:
        """Convert mmCIF _atom_site records to PDB ATOM/HETATM lines."""
        lines = []
        in_atom_site = False
        columns = []
        for line in cif_text.split('\n'):
            if line.startswith('_atom_site.'):
                in_atom_site = True
                columns.append(line.strip().split('.')[1])
                continue
            if in_atom_site and (line.startswith('#') or line.startswith('loop_') or line.startswith('_')):
                in_atom_site = False
                columns = []
                continue
            if not in_atom_site or not line.strip():
                continue

            parts = line.split()
            if len(parts) < len(columns):
                continue

            col = {c: parts[i] if i < len(parts) else '' for i, c in enumerate(columns)}
            record = col.get('group_PDB', 'ATOM')
            if record not in ('ATOM', 'HETATM'):
                continue

            serial = col.get('id', '1')[:5]
            atom_name = col.get('label_atom_id', 'CA')
            res_name = col.get('label_comp_id', 'UNK')
            chain = col.get('auth_asym_id', col.get('label_asym_id', 'A'))
            res_seq = col.get('auth_seq_id', col.get('label_seq_id', '1'))
            x = col.get('Cartn_x', '0.0')
            y = col.get('Cartn_y', '0.0')
            z = col.get('Cartn_z', '0.0')
            occupancy = col.get('occupancy', '1.00')
            b_factor = col.get('B_iso_or_equiv', '0.00')
            element = col.get('type_symbol', atom_name[0])

            # PDB fixed-width format
            atom_field = f" {atom_name:<3s}" if len(atom_name) < 4 else atom_name[:4]
            pdb_line = (
                f"{record:<6s}{serial:>5s} {atom_field} {res_name:>3s} {chain:1s}"
                f"{res_seq:>4s}    {float(x):8.3f}{float(y):8.3f}{float(z):8.3f}"
                f"{float(occupancy):6.2f}{float(b_factor):6.2f}          {element:>2s}"
            )
            lines.append(pdb_line)
        lines.append('END')
        return '\n'.join(lines)

    async def _fetch_uniprot_sequence(self, uniprot_id: str) -> Optional[str]:
        """Fetch protein sequence from UniProt."""
        try:
            url = f"https://rest.uniprot.org/uniprotkb/{uniprot_id}.fasta"
            response = await self.client.get(url, timeout=10.0)

            if response.status_code == 200:
                # Parse FASTA format
                lines = response.text.strip().split("\n")
                sequence = "".join(line for line in lines if not line.startswith(">"))
                return sequence
            return None
        except Exception as e:
            logger.warning(f"Failed to fetch UniProt sequence for {uniprot_id}: {e}")
            return None

    def _parse_pdb_metadata(self, pdb_content: str) -> Dict[str, Any]:
        """Extract metadata from PDB file content."""
        metadata = {}
        chains = set()
        ligands = []

        for line in pdb_content.split("\n")[:500]:  # Only parse header section
            if line.startswith("HEADER"):
                metadata["title"] = line[10:50].strip()
                metadata["pdb_id"] = line[62:66].strip()
            elif line.startswith("TITLE"):
                title = line[10:].strip()
                if "title" in metadata:
                    metadata["title"] += " " + title
                else:
                    metadata["title"] = title
            elif line.startswith("EXPDTA"):
                metadata["method"] = line[10:].strip()
            elif line.startswith("REMARK   2 RESOLUTION"):
                try:
                    resolution = line.split()[-2]
                    metadata["resolution"] = float(resolution)
                except (ValueError, IndexError):
                    pass
            elif line.startswith("SOURCE") and "ORGANISM_SCIENTIFIC" in line:
                metadata["organism"] = line.split(":")[-1].strip().rstrip(";")
            elif line.startswith("ATOM") or line.startswith("HETATM"):
                chain = line[21] if len(line) > 21 else ""
                if chain.strip():
                    chains.add(chain)
                if line.startswith("HETATM"):
                    resname = line[17:20].strip()
                    if resname not in ["HOH", "WAT"] and resname not in ligands:
                        ligands.append(resname)

        metadata["chains"] = sorted(list(chains))
        metadata["ligands"] = ligands[:10]  # Limit to first 10

        return metadata

    async def _execute_get_platform_info(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Get NovoMCP platform information including credit usage."""
        info_type = args.get("info_type", "all")
        org_id = args.get("org_id") or (context.get("org_id") if context else None)

        # Define platform info
        tier_features = {
            "free": {
                "credits_included": 250,
                "tools": "All tools except data connectors (push_to_destination, pull_from_source)",
                "tool_count": 25,
                "description": "30-day free trial — full platform access, 250 credits"
            },
            "enterprise": {
                "credits_included": "Custom",
                "tools": "All tools including data connectors",
                "tool_count": 27,
                "description": "Full platform access + data warehouse integration + custom SLAs + dedicated support"
            }
        }

        database_stats = {
            "molecule_index": "attached when a molecule index service is configured",
            "literature_index": "attached when a Pinecone-backed literature index is configured",
            "patents_index": "attached when a Pinecone-backed patents index is configured",
            "namespaces": ["pubmed", "patents", "preprints", "compounds", "trials", "news"]
        }

        admet_capabilities = {
            "absorption": ["caco2_permeability", "pgp_substrate", "pgp_inhibitor", "bioavailability"],
            "distribution": ["bbb_permeability", "plasma_protein_binding", "vdss"],
            "metabolism": ["cyp1a2_inhibitor", "cyp2c9_inhibitor", "cyp2c19_inhibitor", "cyp2d6_inhibitor", "cyp3a4_inhibitor"],
            "excretion": ["clearance", "half_life"],
            "toxicity": ["hepatotoxicity", "cardiotoxicity_1d", "cardiotoxicity_5d", "cardiotoxicity_10d", "cardiotoxicity_30d", "herg_inhibition", "ames_mutagenicity", "skin_sensitization"],
            "nuclear_receptors": ["ahr_activation", "ar_activation", "er_activation", "ppar_gamma_activation"],
            "total_predictions": 31
        }

        compliance_lists = {
            "dea_schedules": {
                "schedule_i": "High abuse potential, no medical use (heroin, LSD, MDMA)",
                "schedule_ii": "High abuse, severe dependence (fentanyl, oxycodone)",
                "schedule_iii": "Moderate abuse potential (ketamine, steroids)",
                "schedule_iv": "Low abuse potential (benzodiazepines)",
                "schedule_v": "Lowest abuse potential (low-dose codeine)"
            },
            "other_lists": ["CWC chemical weapons", "FDA banned", "EPA PBT", "EU REACH restricted"],
            "scaffold_patterns": "24 controlled substance scaffold patterns detected",
            "fda_approved_whitelist": "FDA-approved drugs automatically pass"
        }

        # Fetch usage data if org_id available and usage requested
        usage_data = None
        if org_id and info_type in ["usage", "all"]:
            usage_data = await self._get_org_usage(org_id)

        # Build response based on info_type
        if info_type == "tiers":
            data = {"subscription_tiers": tier_features}
        elif info_type == "database":
            data = {"database_statistics": database_stats}
        elif info_type == "admet":
            data = {"admet_capabilities": admet_capabilities}
        elif info_type == "compliance":
            data = {"compliance_lists": compliance_lists}
        elif info_type == "usage":
            if usage_data:
                data = {"credit_usage": usage_data}
            else:
                data = {"credit_usage": {"error": "Unable to fetch usage data. Organization ID required."}}
        elif info_type == "update":
            try:
                from core.updater import get_update_status
                data = {"update_status": await get_update_status()}
            except Exception as e:
                data = {"update_status": {"current_version": ENGINE_VERSION, "error": str(e)}}
        else:  # "all"
            update_status = None
            try:
                from core.updater import get_update_status
                update_status = await get_update_status()
            except Exception:
                update_status = {"current_version": ENGINE_VERSION}
            data = {
                "platform": "NovoMCP",
                "version": ENGINE_VERSION,
                "tool_count": len(MCP_TOOLS),
                "description": "Open computational chemistry engine for drug discovery and materials science. Ships with 13 always-available tools (RDKit properties, structural filters, ChEMBL/ClinicalTrials/bioRxiv search, autonomous discovery mode). Additional tools unlock as you configure ADMET, docking, MD, QM, structure-prediction, and compliance services.",
                "update_status": update_status,
                "subscription_tiers": tier_features,
                "database_statistics": database_stats,
                "admet_capabilities": admet_capabilities,
                "compliance_lists": compliance_lists,
                "note": "If tools listed here are not visible, reconnect your MCP connection to refresh the tool list."
            }
            if usage_data:
                data["credit_usage"] = usage_data

        return ToolResult(
            success=True,
            data=data,
            usage={"queries": 0, "tool": "get_platform_info"}  # No query cost for info
        )

    async def _execute_get_credit_usage(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """
        Get credit usage for the authenticated user's organization.
        Returns hybrid billing model: included credits, overage, and value realized.
        1 credit = $1.00 USD
        """
        from datetime import datetime, timedelta

        org_id = context.get("org_id") if context else None

        if not org_id:
            return ToolResult(
                success=False,
                error="Unable to determine your organization. Please ensure you're authenticated with a valid API key.",
                usage={"queries": 0, "tool": "get_credit_usage"}
            )

        # Fetch credit data from dashboard-aggregator
        try:
            response = await self.client.get(
                f"{self.dashboard_url}/mcp/org/{org_id}/usage",
                headers={"X-API-Key": self.dashboard_api_key},
                timeout=10.0
            )

            if response.status_code == 200:
                data = response.json()

                # Basic credit data
                credits_available = float(data.get("credits_available", 0))
                max_credits = float(data.get("max_credits", 1000))
                credits_used = float(data.get("credits_used_total", 0))
                tier = data.get("tier", "free").lower()

                # Get tier billing config
                tier_config = TIER_BILLING.get(tier, TIER_BILLING["free"])
                overage_rate = tier_config["overage_rate"]
                overage_allowed = tier_config["overage_allowed"]

                # For core (pay-as-you-go), credits_included = purchased balance (max_credits from DB)
                # For other tiers, use the fixed monthly allocation from TIER_BILLING
                if tier == "core":
                    credits_included = max_credits
                else:
                    credits_included = tier_config["credits_included"]

                # Calculate overage (credits used beyond included amount this period)
                # Note: credits_used_total is cumulative; we use monthly usage for overage
                credits_used_month = float(data.get("credits_used_this_month", credits_used))
                overage_credits = max(0, credits_used_month - credits_included) if overage_allowed else 0
                overage_cost = overage_credits * overage_rate

                # Value realized (1 credit = $1)
                value_realized = credits_used_month

                # Billing period (current month)
                now = datetime.utcnow()
                period_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                period_end = (period_start + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)

                # Status calculation
                credits_ratio = credits_available / max_credits if max_credits > 0 else 0
                if credits_available <= 0:
                    status = "depleted"
                    alert = "Credits depleted. " + ("Overage charges apply." if overage_allowed else "Upgrade to continue.")
                elif credits_ratio <= 0.2:
                    status = "low"
                    alert = f"Credits running low ({credits_available:.0f} remaining)."
                else:
                    status = "ok"
                    alert = None

                # Build summary with value anchoring
                summary = f"You have realized ${value_realized:.0f} in research value this period. "
                if overage_credits > 0:
                    summary += f"Overage: {overage_credits:.0f} credits (+${overage_cost:.2f}). "
                summary += f"Balance: {credits_available:.0f} credits remaining."

                return ToolResult(
                    success=True,
                    data={
                        # Basic info
                        "org_name": data.get("org_name", "Unknown"),
                        "tier": tier,

                        # Credit balance
                        "credits_available": credits_available,
                        "credits_used_total": credits_used,
                        "max_credits": max_credits,
                        "usage_percent": round((credits_used_month / credits_included * 100) if credits_included > 0 else 0, 1),
                        "credits_remaining_percent": round(credits_ratio * 100, 1),

                        # Hybrid billing model fields
                        "credits_included": credits_included,
                        "overage_rate": overage_rate,
                        "overage_credits": overage_credits,
                        "overage_cost": overage_cost,
                        "value_realized": value_realized,

                        # Billing period
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),

                        # Status
                        "status": status,
                        "alert": alert,
                        "summary": summary,
                    },
                    usage={"queries": 0, "tool": "get_credit_usage"}
                )
            else:
                error_detail = response.text[:500] if response.text else "No response body"
                logger.warning(f"Credit usage fetch returned {response.status_code} for org {org_id}: {error_detail}")
                return ToolResult(
                    success=False,
                    error=f"Unable to fetch credit data (status {response.status_code}). URL: {self.dashboard_url}/mcp/org/{org_id}/usage",
                    usage={"queries": 0, "tool": "get_credit_usage"}
                )

        except Exception as e:
            logger.error(f"Error fetching credit usage for org {org_id}: {e}")
            return ToolResult(
                success=False,
                error=f"Error connecting to credit service: {str(e)[:200]}",
                usage={"queries": 0, "tool": "get_credit_usage"}
            )

    # =========================================================================
    # Pro Tier Tools
    # =========================================================================

    async def _execute_search_similar(self, args: Dict[str, Any]) -> ToolResult:
        """Search for similar molecules in enriched database."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        try:
            # Backend expects "threshold" and "limit", not "min_similarity" and "top_k"
            response = await self._call_service(
                "faves-compliance",
                "/api/search/similar",
                {
                    "smiles": smiles,
                    "limit": min(args.get("top_k", 10), 100),
                    "threshold": args.get("min_similarity", 0.7),
                    "exclude_controlled": args.get("exclude_controlled", False),
                    "exclude_flagged": args.get("exclude_flagged", False),
                },
                # TEMPORARY: 120s timeout while Cosmos DB brute-force scan is in use.
                # Revert to 60s after DiskANN vector search is wired up (~April 2026).
                # See: faves-compliance/PENDING-CONVERGENCE.md
                timeout=30.0  # DiskANN backend — sub-second (was 120s brute-force)
            )

            if response.status_code == 200:
                return ToolResult(
                    success=True,
                    data=response.json(),
                    usage={"queries": 1, "tool": "search_similar"}
                )
            return ToolResult(success=False, error=f"Search error: {response.status_code}")

        except Exception as e:
            return ToolResult(success=False, error=f"Similarity search failed: {str(e)}")

    # =========================================================================
    # Tree-Guided Retrieval Handlers
    # =========================================================================

    def _wrap_tree_result(self, result: dict) -> ToolResult:
        """Convert TreeSearchExecutor dict to ToolResult."""
        if result.get("success"):
            return ToolResult(success=True, data=result["data"])
        return ToolResult(success=False, error=result.get("error", "Unknown error"))

    async def _execute_explore_chemical_space(self, args: Dict[str, Any]) -> ToolResult:
        return self._wrap_tree_result(await self.tree_search.execute_explore_chemical_space(args))

    async def _execute_drill_into_cluster(self, args: Dict[str, Any]) -> ToolResult:
        return self._wrap_tree_result(await self.tree_search.execute_drill_into_cluster(args))

    async def _execute_compare_candidates(self, args: Dict[str, Any]) -> ToolResult:
        return self._wrap_tree_result(await self.tree_search.execute_compare_candidates(args))

    async def _execute_vector_search(self, args: Dict[str, Any]) -> ToolResult:
        return self._wrap_tree_result(await self.tree_search.execute_vector_search(args))

    async def _execute_filter_molecules(self, args: Dict[str, Any]) -> ToolResult:
        """Filter molecules from enriched database."""
        filters = args.get("filters", {})
        limit = min(args.get("limit", 10), 100)
        offset = args.get("offset", 0)

        try:
            # Flatten filters dict - backend expects flat fields, not nested
            request_body = {**filters, "limit": limit, "offset": offset}

            response = await self._call_service(
                "faves-compliance",
                "/api/search/filter",
                request_body,
                timeout=60.0
            )

            if response.status_code == 200:
                return ToolResult(
                    success=True,
                    data=response.json(),
                    usage={"queries": 1, "tool": "filter_molecules"}
                )
            return ToolResult(success=False, error=f"Filter error: {response.status_code}")

        except Exception as e:
            return ToolResult(success=False, error=f"Filter query failed: {str(e)}")

    async def _execute_batch_profile(self, args: Dict[str, Any]) -> ToolResult:
        """Get profiles for multiple molecules.

        Internally chunks the smiles_list and processes each chunk with
        bounded concurrency (asyncio.gather within the chunk, serial between
        chunks). Previous implementation was a strict serial loop — on a
        batch of 50+ molecules where most were novel (and therefore needed
        both a FAVES call AND an RDKit pass each), total latency exceeded
        the 30s upstream timeout and Claude saw a TIMEOUT error.

        Concurrency knobs:
          CHUNK_SIZE — max molecules per wave. 25 is the sweet spot — keeps
            any single chunk's gather() under ~3s for typical payloads while
            bounding the peak in-flight concurrency on Cosmos + faves-compliance.
          (No explicit semaphore — CHUNK_SIZE IS the concurrency cap, and
          serial chunks apply natural backpressure.)
        """
        smiles_list = args.get("smiles_list", [])
        # Type guard: reject string inputs with a clear error rather than
        # iterating over them character-by-character (observed 2026-04-21
        # with permissive inputSchema). The TS-side z.array(z.string())
        # prevents this at the protocol layer; this is the backstop for
        # any direct-API caller that sends the wrong shape.
        if isinstance(smiles_list, str):
            return ToolResult(
                success=False,
                error="smiles_list must be a JSON array of SMILES strings, not a string. Example: [\"CCO\", \"CC(=O)O\"]"
            )
        if not isinstance(smiles_list, list):
            return ToolResult(
                success=False,
                error=f"smiles_list must be an array of SMILES strings, got {type(smiles_list).__name__}"
            )
        if not smiles_list:
            return ToolResult(success=False, error="Missing required parameter: smiles_list")
        if len(smiles_list) > 100:
            return ToolResult(success=False, error="Batch size exceeds maximum (100)")

        CHUNK_SIZE = 25

        # ADMET in one call: route each molecule through get_molecule_profile so
        # the per-molecule entry carries the same ADMET block (toxicity,
        # metabolism, nuclear_receptors, stress_response) that screen_library
        # returns — single-call semantics match the tool name. include_admet
        # (default True) gates the on-the-fly addie-models call for novel
        # molecules; set False for fast properties-only at lower credit cost.
        include_admet = args.get("include_admet", True)

        async def profile_one(smiles: str) -> Dict[str, Any]:
            """Phase 1: profile a single molecule WITHOUT the per-molecule novel
            addie call (include_admet=False). Known molecules still carry their
            pre-computed corpus ADMET; novel molecules get their ADMET filled in by
            the single batched addie call in phase 2 below. Exceptions are captured
            so one bad SMILES doesn't fail the batch."""
            try:
                res = await self._execute_get_molecule_profile(
                    {"smiles": smiles, "include_admet": False}
                )
                if res.success and res.data is not None:
                    return res.data
                return {
                    "smiles": smiles,
                    "source": "error",
                    "in_database": False,
                    "error": (res.error or "profile failed")[:200],
                }
            except Exception as e:
                logger.warning(f"batch_profile: failed to profile {smiles[:40]}: {e}")
                return {
                    "smiles": smiles,
                    "source": "error",
                    "in_database": False,
                    "error": str(e)[:200],
                }

        results: List[Dict[str, Any]] = []
        for i in range(0, len(smiles_list), CHUNK_SIZE):
            chunk = smiles_list[i : i + CHUNK_SIZE]
            chunk_results = await asyncio.gather(*(profile_one(s) for s in chunk))
            results.extend(chunk_results)

        # Phase 2 (WS2): one batched addie call. Novel molecules get their full ADMET;
        # known molecules also go through addie for the Phase-1 BRIDGE — the retrained
        # heads (nr/sr/clinical/cardiotox_dict) are corpus-stale, so we overlay them
        # live onto the corpus admet. Drops back to free corpus reads once Phase 4 lands.
        if include_admet:
            novel_smiles = [
                r["smiles"] for r in results
                if r.get("in_database") is False and r.get("source") != "error" and r.get("smiles")
            ]
            known_smiles = [
                r["smiles"] for r in results if r.get("in_database") is True and r.get("smiles")
            ]
            if novel_smiles or known_smiles:
                admet_map = await self._predict_admet_batch_addie(novel_smiles + known_smiles)
                for r in results:
                    block = admet_map.get(r.get("smiles"))
                    if block is None:
                        continue
                    if r.get("in_database") is True:
                        corpus = r.get("admet") if isinstance(r.get("admet"), dict) else {}
                        r["admet"] = _overlay_bridge_live(corpus, block)
                    else:
                        r["admet"] = block
                        r["admet_available"] = True
                        r["source"] = "computed+admet"
                        r.pop("note", None)

        known_count = sum(1 for r in results if r.get("in_database") is True)
        novel_count = sum(1 for r in results if r.get("in_database") is False and r.get("source") != "error")
        error_count = sum(1 for r in results if r.get("source") == "error")

        return ToolResult(
            success=True,
            data={
                "total": len(results),
                "known_molecules": known_count,
                "novel_molecules": novel_count,
                **({"errors": error_count} if error_count > 0 else {}),
                "results": results,
            },
            usage={"queries": len(smiles_list), "tool": "batch_profile"},
        )

    # =========================================================================
    # Team Tier Tools - Optimization & Structure
    # =========================================================================

    async def _execute_optimize_molecule(self, args: Dict[str, Any]) -> ToolResult:
        """Optimize molecule with auto-FAVES check on outputs."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        raw_objectives = args.get("objectives", {"qed": 0.8, "logp": 3.0})
        num_variants = min(args.get("num_variants", 10), 50)
        exclude_controlled = args.get("exclude_controlled", True)

        # Configurable Tanimoto filtering (Theo P1) — same schema as lead_optimization
        sim_range = args.get("similarity_range") or {}
        sim_min = float(sim_range.get("min", 0.3))
        sim_max = float(sim_range.get("max", 0.85))
        if sim_min < 0 or sim_max > 1 or sim_min >= sim_max:
            return ToolResult(
                success=False,
                error=f"Invalid similarity_range: min={sim_min}, max={sim_max}."
            )
        pr_thresholds = args.get("patent_risk_thresholds") or {}
        pr_low = float(pr_thresholds.get("low", 0.4))
        pr_high = float(pr_thresholds.get("high", 0.7))
        if pr_low < 0 or pr_high > 1 or pr_low >= pr_high:
            return ToolResult(
                success=False,
                error=f"Invalid patent_risk_thresholds: low={pr_low}, high={pr_high}."
            )

        # Convert flat objectives {"qed": 0.8} to nested format {"qed": {"target": 0.8, "weight": 1.0}}
        objectives = {}
        for key, value in raw_objectives.items():
            if isinstance(value, dict):
                objectives[key] = value  # Already nested
            else:
                objectives[key] = {"target": float(value), "weight": 1.0}

        try:
            # Call MolMIM optimizer
            response = await self._call_service(
                "molmim-optimizer",
                "/molmim-optimizer/optimize",
                {
                    "smiles": smiles,
                    "objectives": objectives,
                    "num_molecules": num_variants,
                    "iterations": 10
                },
                timeout=60.0
            )

            if response.status_code != 200:
                return ToolResult(success=False, error=f"Optimizer error: {response.status_code}")

            opt_result = response.json()
            # molmim-optimizer returns "optimized_molecules"
            variants = opt_result.get("optimized_molecules") or opt_result.get("molecules", [])

            # No variants — do not charge credits for null results
            if not variants:
                return ToolResult(
                    success=True,
                    data={
                        "input_smiles": smiles,
                        "variants_generated": 0,
                        "variants_compliant": 0,
                        "variants_filtered": 0,
                        "variants": [],
                        "message": (
                            "MolMIM did not generate variants for this input. The SMILES may "
                            "be outside the model's training distribution (unusual scaffolds, "
                            "extreme size, or invalid structures). Try lead_optimization "
                            "(scaffold_hop) for structurally diverse alternatives."
                        ),
                        "credits_refunded": True,
                    },
                    usage={"queries": 0, "tool": "optimize_molecule", "_dynamic_credits": 0}
                )

            # Batch pairwise Tanimoto: seed vs all variants (single call)
            variant_smiles_list = [
                v.get("smiles", "") for v in variants if v.get("smiles")
            ]
            patent_risk_map = {}
            if variant_smiles_list:
                try:
                    tc_resp = await self._call_service(
                        "faves-compliance", "/api/similarity/pairwise",
                        {"smiles_a": smiles, "smiles_b": variant_smiles_list},
                        timeout=30.0
                    )
                    if tc_resp.status_code == 200:
                        for comp in tc_resp.json().get("comparisons", []):
                            patent_risk_map[comp["smiles"]] = {
                                "tanimoto_to_seed": comp.get("tanimoto"),
                                "patent_risk": comp.get("patent_risk"),
                            }
                except Exception:
                    pass

            # FAVES check + property extraction on all variants
            checked_variants = []
            flagged_count = 0

            for variant in variants:
                variant_smiles = variant.get("smiles")
                if not variant_smiles:
                    continue

                faves = await self._faves_context_free(variant_smiles)
                compliance = faves.get("compliance", {})
                is_flagged = compliance.get("status") in ["controlled", "flagged"]

                if exclude_controlled and is_flagged:
                    flagged_count += 1
                    continue

                # Extract properties from the FAVES /api/classify response
                # (faves-compliance calculates these internally via chem-props)
                raw = faves.get("raw", {})
                tox = raw.get("toxicity_summary", {})

                rot_bonds = tox.get("rotatable_bonds") or 0
                tpsa_val = tox.get("tpsa") or 0
                veber = (1 if rot_bonds > 10 else 0) + (1 if tpsa_val > 140 else 0)

                # Compute modification description via MCS diff
                mod_desc = None
                try:
                    from rdkit import Chem
                    from rdkit.Chem import rdFMCS
                    seed_mol = Chem.MolFromSmiles(smiles)
                    var_mol = Chem.MolFromSmiles(variant_smiles)
                    if seed_mol and var_mol:
                        mcs = rdFMCS.FindMCS([seed_mol, var_mol], timeout=2)
                        if mcs.numAtoms > 0:
                            added = var_mol.GetNumHeavyAtoms() - mcs.numAtoms
                            removed = seed_mol.GetNumHeavyAtoms() - mcs.numAtoms
                            parts = []
                            if removed > 0:
                                parts.append(f"-{removed} atoms")
                            if added > 0:
                                parts.append(f"+{added} atoms")
                            if parts:
                                mod_desc = ", ".join(parts) + f" (MCS {mcs.numAtoms})"
                            else:
                                mod_desc = "isomer / stereochemistry change"
                except Exception:
                    pass

                # Prior art check via Redis SMILES index.
                # Always emit a prior_art dict with a disclosed sentinel so the viewer
                # can distinguish "lookup didn't run" (null) from "novel" (false).
                prior_art_obj = {
                    "disclosed": None,
                    "pubchem_cid": None,
                    "disclosure_source": None,
                    "inchikey": None,
                }
                try:
                    r = await self._get_redis()
                    if r:
                        canon_var = Chem.MolToSmiles(Chem.MolFromSmiles(variant_smiles))
                        cid = await r.get(f"smiles:{canon_var}")
                        if cid:
                            prior_art_obj = {
                                "disclosed": True,
                                "pubchem_cid": cid.decode() if isinstance(cid, bytes) else cid,
                                "disclosure_source": "PubChem (Redis index)",
                                "inchikey": None,
                            }
                        else:
                            # Key miss. The AWS SMILES index is only ~22% backfilled
                            # (~53M of 244M keys, 2026-06-07), so a miss does NOT prove
                            # novelty — the molecule may simply be un-indexed. Report
                            # unknown (None), never False, to avoid a false patent-
                            # clearance signal. Flip to disclosed=False on miss once
                            # the backfill completes.
                            prior_art_obj = {
                                "disclosed": None,
                                "pubchem_cid": None,
                                "disclosure_source": "index_incomplete",
                                "inchikey": None,
                            }
                except Exception:
                    pass

                enriched_variant = {
                    **variant,
                    "mw": tox.get("molecular_weight") or variant.get("mw"),
                    "logp": tox.get("logp") or variant.get("logp"),
                    "tpsa": tpsa_val or variant.get("tpsa"),
                    "qed": raw.get("qed") or variant.get("qed"),
                    # SA via local RDKit sascorer — chem-props returns an unreliable
                    # value (flat 1.0), so compute the real Ertl-Schuffenhauer score
                    # here (resolves the 2026-06-05 sa_score-uniformity bug).
                    "sa_score": _compute_sa_score(variant_smiles) or raw.get("synthetic_accessibility") or variant.get("sa_score"),
                    "hbd": tox.get("hbd"),
                    "hba": tox.get("hba"),
                    "rotatable_bonds": rot_bonds,
                    "lipinski_violations": tox.get("lipinski_violations"),
                    "veber_violations": veber,
                    "compliance_status": compliance.get("status", "unchecked"),
                    "is_compliant": not is_flagged,
                    "modification": mod_desc,
                    "prior_art": prior_art_obj,
                }
                # Apply patent risk from batch Tanimoto
                # Try both raw and canonical SMILES keys (services may canonicalize)
                pr = patent_risk_map.get(variant_smiles)
                if not pr:
                    # Try canonical form via RDKit
                    try:
                        from rdkit import Chem
                        canon = Chem.MolToSmiles(Chem.MolFromSmiles(variant_smiles))
                        pr = patent_risk_map.get(canon)
                    except Exception:
                        pass
                if pr:
                    enriched_variant.update(pr)
                checked_variants.append(enriched_variant)

            # Configurable Tanimoto filtering + patent_risk reclassification (Theo P1)
            filtered_by_similarity = 0
            using_custom_pr_thresholds = not (pr_low == 0.4 and pr_high == 0.7)
            kept_variants: List[Dict[str, Any]] = []
            for v in checked_variants:
                tc = v.get("tanimoto_to_seed")
                if using_custom_pr_thresholds and isinstance(tc, (int, float)):
                    if tc >= pr_high:
                        v["patent_risk"] = "high"
                    elif tc >= pr_low:
                        v["patent_risk"] = "low"
                    else:
                        v["patent_risk"] = "novel"
                if isinstance(tc, (int, float)) and (tc < sim_min or tc > sim_max):
                    filtered_by_similarity += 1
                    continue
                kept_variants.append(v)
            checked_variants = kept_variants

            # Build seed properties using the same FAVES lookup as variants
            seed_faves = await self._faves_context_free(smiles)
            seed_raw = seed_faves.get("raw", {})
            seed_tox = seed_raw.get("toxicity_summary", {})
            seed_compliance = seed_faves.get("compliance", {})
            seed_rot = seed_tox.get("rotatable_bonds") or 0
            seed_tpsa = seed_tox.get("tpsa") or 0
            seed_obj = {
                "smiles": smiles,
                "mw": seed_tox.get("molecular_weight"),
                "logp": seed_tox.get("logp"),
                "tpsa": seed_tpsa,
                "qed": seed_raw.get("qed"),
                "sa_score": _compute_sa_score(smiles) or seed_raw.get("synthetic_accessibility"),
                "hbd": seed_tox.get("hbd"),
                "hba": seed_tox.get("hba"),
                "rotatable_bonds": seed_rot,
                "lipinski_violations": seed_tox.get("lipinski_violations"),
                "veber_violations": (1 if seed_rot > 10 else 0) + (1 if seed_tpsa > 140 else 0),
                "compliance_status": seed_compliance.get("status", "unchecked"),
            }

            result_payload = {
                "input_smiles": smiles,
                "seed": seed_obj,
                "variants_generated": len(variants),
                "variants_compliant": len(checked_variants),
                "variants_filtered": flagged_count,
                "variants": checked_variants,
                # Configurable Tanimoto filtering
                "similarity_range": {"min": sim_min, "max": sim_max},
                "patent_risk_thresholds": {"low": pr_low, "high": pr_high},
                "filtered_by_similarity": filtered_by_similarity,
                "tool_suggestions": [
                    self._tool_suggestion(
                        "predict_admet",
                        "Get detailed ADMET predictions (40+ properties) for promising variants"
                    ),
                    self._tool_suggestion(
                        "get_3d_properties",
                        "Analyze 3D molecular properties (geometry, energy, electrostatics) for lead candidates"
                    )
                ]
            }
            if filtered_by_similarity > 0:
                result_payload["similarity_filter_note"] = (
                    f"{filtered_by_similarity} variant(s) filtered because Tanimoto to seed "
                    f"fell outside [{sim_min}, {sim_max}]."
                )

            return ToolResult(
                success=True,
                data=result_payload,
                usage={"queries": 1 + len(variants), "tool": "optimize_molecule"}
            )

        except Exception as e:
            # Surface type + repr to avoid the "empty reason" anti-pattern
            # (some httpx / network exceptions have empty str(); use repr()
            # as a fallback so the actual cause is visible to the caller).
            msg = str(e) or repr(e) or "no message"
            logger.exception("optimize_molecule failed")
            return ToolResult(
                success=False,
                error=f"Optimization failed: {type(e).__name__}: {msg}",
            )

    async def _execute_predict_structure(self, args: Dict[str, Any]) -> ToolResult:
        """Submit structure prediction job with automatic wait-and-poll for fast completions.

        Ergonomic top-level shortcuts: a bare `sequence` string auto-wraps into
        a single protein molecule entry; a bare `smiles` string auto-wraps into
        a single ligand. This matches what LLMs and users naturally pass for
        single-molecule predictions without requiring the verbose molecules-array
        format (which is still supported for multi-chain / complex predictions).
        """
        import asyncio

        molecules = args.get("molecules", [])

        # Convenience: auto-wrap top-level sequence/smiles into molecules array
        if not molecules:
            top_seq = args.get("sequence")
            top_smiles = args.get("smiles")
            inferred = []
            if top_seq and isinstance(top_seq, str):
                # Infer type: nucleotide alphabet (ACGTU + N) → dna/rna, else protein
                seq_upper = top_seq.upper().strip()
                if seq_upper and set(seq_upper) <= set("ACGTUN"):
                    mol_type = "rna" if "U" in seq_upper else "dna"
                else:
                    mol_type = "protein"
                inferred.append({"type": mol_type, "id": "target", "sequence": top_seq})
            if top_smiles and isinstance(top_smiles, str):
                inferred.append({"type": "ligand", "id": f"ligand_{len(inferred)+1}", "smiles": top_smiles})
            if inferred:
                molecules = inferred

        if not molecules:
            return ToolResult(
                success=False,
                error=(
                    "Missing required parameter: molecules. Pass either "
                    "`molecules: [{type, id, sequence|smiles}, ...]` or a "
                    "top-level `sequence` (protein/DNA/RNA) / `smiles` (ligand) "
                    "for single-molecule predictions."
                ),
            )

        # Configurable wait parameters
        max_wait_seconds = 60  # Maximum time to wait for completion
        poll_interval = 5      # Seconds between status checks
        initial_delay = 2      # Wait before first poll (job registration)

        try:
            # Step 1: Submit the job
            response = await self._call_service(
                "openfold3",
                "/predict",
                {
                    "molecules": molecules,
                    "output_format": args.get("output_format", "pdb")
                },
                timeout=30.0
            )

            if response.status_code != 200:
                return ToolResult(success=False, error=f"Structure service error: {response.status_code}")

            data = response.json()
            job_id = data.get("job_id")

            if not job_id:
                return ToolResult(success=False, error="No job ID returned from service")

            # Step 2: Wait for job registration
            await asyncio.sleep(initial_delay)

            # Step 3: Poll for completion (up to max_wait_seconds)
            elapsed = initial_delay
            while elapsed < max_wait_seconds:
                try:
                    result_response = await self._call_service(
                        "openfold3", f"/result/{job_id}", {}, method="GET", timeout=10.0
                    )

                    if result_response.status_code == 200:
                        # Job completed - return full result
                        result_data = result_response.json()

                        # Normalize field name: OpenFold3 returns 'structure', viewer expects 'pdb_data'
                        if "structure" in result_data and "pdb_data" not in result_data:
                            result_data["pdb_data"] = result_data.pop("structure")

                        return ToolResult(
                            success=True,
                            data={
                                "job_id": job_id,
                                "status": "completed",
                                **result_data
                            },
                            usage={"queries": 1, "tool": "predict_structure"}
                        )
                    elif result_response.status_code == 202:
                        # Still running - continue polling
                        pass
                    elif result_response.status_code == 404:
                        # Job not yet registered - wait a bit more
                        pass
                    else:
                        # Unexpected status - continue polling
                        logger.warning(f"Unexpected status {result_response.status_code} while polling job {job_id}")

                except Exception as poll_error:
                    logger.warning(f"Polling error for job {job_id}: {poll_error}")

                await asyncio.sleep(poll_interval)
                elapsed += poll_interval

            # Step 4: Timeout reached - return job_id for manual polling
            return ToolResult(
                success=True,
                data={
                    "job_id": job_id,
                    "status": "running",
                    "message": f"Structure prediction still running after {max_wait_seconds}s. Use get_structure_result with job_id '{job_id}' to check status and retrieve results.",
                    "estimated_remaining": "30-120 seconds for typical proteins"
                },
                usage={"queries": 1, "tool": "predict_structure"}
            )

        except Exception as e:
            return ToolResult(success=False, error=f"Structure prediction failed: {str(e)}")

    async def _execute_get_structure_result(self, args: Dict[str, Any]) -> ToolResult:
        """Universal job result checker. Routes to the correct service based on job_id prefix."""
        job_id = args.get("job_id")
        if not job_id:
            return ToolResult(success=False, error="Missing required parameter: job_id")

        service = args.get("service", "auto")

        # Route ALL non-OpenFold3 jobs to get_job_status
        # This tool is now the primary entry point for checking any async job
        if job_id.startswith(("gro_", "dock_", "dock_batch_", "qc_", "lo_")):
            return await self._execute_get_job_status({"job_id": job_id, "service": service})
        if service != "auto" and service != "openfold3":
            return await self._execute_get_job_status({"job_id": job_id, "service": service})

        try:
            response = await self._call_service(
                "openfold3", f"/result/{job_id}", {}, method="GET"
            )

            if response.status_code == 200:
                result_data = response.json()

                # Normalize field name: OpenFold3 returns 'structure', viewer expects 'pdb_data'
                if "structure" in result_data and "pdb_data" not in result_data:
                    result_data["pdb_data"] = result_data.pop("structure")

                return ToolResult(
                    success=True,
                    data=result_data,
                    usage={"queries": 0, "tool": "get_structure_result"}
                )
            elif response.status_code == 202:
                return ToolResult(
                    success=True,
                    data={"status": "running", "message": "Prediction still in progress"},
                    usage={"queries": 0, "tool": "get_structure_result"}
                )
            elif response.status_code == 404:
                # Might be a non-OpenFold3 job — try get_job_status as fallback
                return await self._execute_get_job_status({"job_id": job_id, "service": "auto"})
            return ToolResult(success=False, error=f"Status check error: {response.status_code}")

        except Exception as e:
            return ToolResult(success=False, error=f"Status check failed: {str(e)}")

    # =========================================================================
    # File Intelligence Layer — upload once, reference everywhere
    # =========================================================================

    async def _execute_generate_upload_url(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Generate a signed upload URL for large files."""
        filename = args.get("filename")
        file_type = args.get("file_type", "custom")
        auto_process = args.get("auto_process")  # Optional: auto-trigger tool on upload

        if not filename:
            return ToolResult(success=False, error="Missing required parameter: filename")

        # Validate auto_process.tool against the live tool registry BEFORE returning
        # a presigned URL. Without this check, a typo'd or unknown tool name sails
        # through, the user uploads the file, and the dispatcher then fails after
        # the upload has already cost time + bandwidth. Reject up front so the LLM
        # can correct the call cheaply (Guard 1 regression).
        if auto_process and isinstance(auto_process, dict):
            ap_tool = auto_process.get("tool")
            if not ap_tool:
                return ToolResult(
                    success=False,
                    error="auto_process.tool is required when auto_process is set",
                )
            if ap_tool not in TOOL_CREDITS:
                # TOOL_CREDITS is the canonical name registry — every dispatch
                # site resolves through it for credit cost lookup.
                return ToolResult(
                    success=False,
                    error=(
                        f"auto_process.tool '{ap_tool}' is not a registered tool. "
                        f"Did you mean one of the auto-processable tools "
                        f"(audit_system, parameterize_metal, predict_admet, "
                        f"check_compliance, ...)? See the tool list for valid names."
                    ),
                )

        user = context or {}
        org_id = user.get("org_id", "unknown")
        user_id = user.get("user_id", "unknown")
        upload_source = user.get("upload_source", "mcp")

        # Build metadata with auto-process instructions if provided
        metadata = {}
        if auto_process and isinstance(auto_process, dict):
            metadata["auto_process"] = auto_process

        try:
            if not self._file_client:
                return ToolResult(
                    success=False,
                    error="File upload service not configured. Contact support.",
                )

            result = await self._file_client.generate_upload_url(
                org_id=org_id,
                user_id=user_id,
                filename=filename,
                file_type=file_type,
                upload_source=upload_source,
                metadata=metadata if metadata else None,
            )

            # Build the hosted upload page URL. Short + LLM-safe: the page fetches
            # the presigned URL itself from GET /files/{file_id}/upload-url. We no
            # longer embed it in a #u=<base64> fragment — that ~700-char SigV4 URL
            # gets truncated by LLMs when they surface the link (broke uploads after
            # the Azure→AWS move, where SigV4 URLs are longer than Azure SAS).
            file_id = result["file_id"]
            hosted_url = f"https://app.novomcp.com/upload/{file_id}"

            # Estimate processing time if auto-process is configured
            from core.file_intelligence import estimate_processing_time
            est_minutes = None
            if auto_process:
                # Estimate based on file type (size unknown yet — use type defaults)
                est_minutes = estimate_processing_time(file_type, 0)

            auto_msg = ""
            if auto_process:
                tool = auto_process.get("tool", "the configured tool")
                auto_msg = (
                    f"\n\nAuto-processing is enabled: once your upload completes, "
                    f"{tool} will start automatically. "
                    f"{'Estimated processing time: ~' + str(est_minutes) + ' minutes. ' if est_minutes else ''}"
                    f"You will receive an email notification when processing completes. "
                    f"You can continue your conversation or check back later."
                )

            return ToolResult(
                success=True,
                data={
                    "file_id": file_id,
                    "upload_url": hosted_url,
                    "direct_upload_url": result["upload_url"],
                    "expires_at": result["expires_at"],
                    "max_size_bytes": result.get("max_size_bytes"),
                    "auto_process_enabled": bool(auto_process),
                    "estimated_processing_minutes": est_minutes,
                    "instructions": (
                        f"Upload your file at: {hosted_url}\n"
                        f"Or PUT directly to the upload URL.\n"
                        f"File ID: {file_id} — you can reference this in any conversation.\n"
                        f"Upload URL expires at {result['expires_at']}."
                        f"{auto_msg}"
                    ),
                    "message": (
                        f"Upload URL generated for {filename} ({file_type}). "
                        f"File ID: {file_id}"
                    ),
                },
                usage={"queries": 0, "tool": "generate_upload_url"},
            )

        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except Exception as e:
            logger.exception(f"Error generating upload URL: {e}")
            return ToolResult(success=False, error=f"Upload URL generation failed: {str(e)[:300]}")

    async def _execute_get_file_status(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Check the status of an uploaded file."""
        file_id = args.get("file_id")
        if not file_id:
            return ToolResult(success=False, error="Missing required parameter: file_id")

        user = context or {}
        org_id = user.get("org_id", "unknown")

        try:
            if not self._file_client:
                return ToolResult(success=False, error="File service not configured.")

            result = await self._file_client.get_file_status(file_id, org_id)
            if not result:
                return ToolResult(
                    success=False,
                    error=f"File {file_id} not found. Check the file ID and try again.",
                )

            # Auto-process: if the upload just completed and there are
            # processing instructions, dispatch in the background.
            auto = result.get("auto_process") if isinstance(result, dict) else None
            if not auto:
                # Also check the metadata directly (confirm_upload returns it)
                meta = result.get("metadata", {})
                auto = meta.get("auto_process") if isinstance(meta, dict) else None

            if auto and result.get("status") == "processing":
                import asyncio
                asyncio.create_task(
                    self._dispatch_auto_process(file_id, org_id, auto, user)
                )
                est = result.get("estimated_processing_minutes") or meta.get("estimated_processing_minutes")
                result["auto_process_status"] = "dispatched"
                result["message"] = (
                    f"File uploaded successfully. Processing has started automatically "
                    f"using {auto.get('tool', 'the configured tool')}. "
                    f"{'Estimated time: ~' + str(est) + ' minutes. ' if est else ''}"
                    f"You will receive an email when processing completes. "
                    f"You can continue your conversation or check back with "
                    f"file ID: {file_id}"
                )

            # Cross-surface enrichment: if there are child files (outputs
            # derived from this file), include them for provenance queries.
            # "What happened with file X?" → shows the full DAG.
            try:
                children = await self._file_client.list_files(
                    org_id=org_id, limit=10,
                )
                child_files = [
                    f for f in children.get("files", [])
                    if f.get("parent_file_id") == file_id
                ]  # Note: this is a client-side filter; could be a Cosmos query
                if child_files:
                    result["derived_files"] = child_files
            except Exception:
                pass  # Best-effort enrichment

            # Generate download URL if file is uploaded/completed
            if result.get("status") in ("uploaded", "completed"):
                try:
                    download_url = await self._file_client.generate_download_url(
                        file_id, org_id, ttl_minutes=60
                    )
                    if download_url:
                        result["download_url"] = download_url
                        result["download_expires_in_minutes"] = 60
                except Exception:
                    pass  # Best-effort

            return ToolResult(
                success=True,
                data=result,
                usage={"queries": 0, "tool": "get_file_status"},
            )

        except Exception as e:
            logger.exception(f"Error getting file status: {e}")
            return ToolResult(success=False, error=f"File status check failed: {str(e)[:300]}")

    async def _execute_list_files(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """List files uploaded to the user's organization."""
        user = context or {}
        org_id = user.get("org_id", "unknown")
        file_type = args.get("file_type")
        status = args.get("status")
        limit = min(int(args.get("limit", 50)), 100)

        try:
            if not self._file_client:
                return ToolResult(success=False, error="File service not configured.")

            result = await self._file_client.list_files(
                org_id=org_id,
                file_type=file_type,
                status=status,
                limit=limit,
            )

            return ToolResult(
                success=True,
                data=result,
                usage={"queries": 0, "tool": "list_files"},
            )

        except Exception as e:
            logger.exception(f"Error listing files: {e}")
            return ToolResult(success=False, error=f"File listing failed: {str(e)[:300]}")

    # =========================================================================
    # File Auto-Process Dispatcher
    # =========================================================================

    async def _dispatch_auto_process(
        self,
        file_id: str,
        org_id: str,
        auto_process: Dict[str, Any],
        user_context: Dict[str, Any],
    ) -> None:
        """Background dispatcher for auto-processing uploaded files.

        Called when an upload completes and the file record has
        auto_process metadata. Dispatches to the appropriate tool
        executor, updates the file record on completion/failure,
        and triggers email notification.

        Runs as an asyncio.create_task — does not block the upload
        confirmation response.
        """
        tool_name = auto_process.get("tool", "")
        tool_args = dict(auto_process.get("args", {}))

        # Inject the file reference using the field name appropriate for the tool.
        # Default to qm_file_id (parameterize_metal Phase 2). Tools that accept
        # files by other field names should specify inject_as in auto_process.
        inject_field = auto_process.get("inject_as", "qm_file_id")
        tool_args[inject_field] = file_id

        logger.info(
            f"Auto-process dispatched: file={file_id}, tool={tool_name}, "
            f"args_keys={list(tool_args.keys())}"
        )

        try:
            # Execute the tool
            handler = getattr(self, f"_execute_{tool_name}", None)
            if not handler:
                raise ValueError(f"Unknown auto-process tool: {tool_name}")

            result = await handler(tool_args, context=user_context)

            # Decide the file's terminal status. For ASYNC tools (e.g.
            # parameterize_metal) the executor returns success=True the moment
            # the job is ACCEPTED — data carries a job_id and a non-terminal
            # status ("submitted"/"running"). That is NOT completion. Marking the
            # file completed here was the bug behind "status: completed,
            # derived_files: 0" when the MCPB.py job later failed. So for async
            # jobs we link the job and wait for its terminal state before
            # finalizing, so the file mirrors the actual downstream outcome.
            data = result.data if (result and isinstance(result.data, dict)) else {}
            job_id = data.get("job_id")
            job_status = str(data.get("status") or "").lower()
            _PENDING = {"submitted", "queued", "running", "processing", "pending"}

            if result.success and job_id and job_status in _PENDING:
                await self._file_client.link_tool_call(
                    file_id, org_id, tool_name, job_id=job_id
                )
                terminal = await self._await_job_terminal(job_id, user_context)
                if terminal["status"] == "completed":
                    await self._file_client.complete_processing(
                        file_id, org_id, results=terminal.get("result") or {},
                    )
                    outcome, message = "completed", (
                        f"Your file has been processed successfully by {tool_name}."
                    )
                    logger.info(
                        f"Auto-process job complete: file={file_id}, "
                        f"tool={tool_name}, job={job_id}"
                    )
                else:
                    err = terminal.get("error") or "Processing failed"
                    await self._file_client.fail_processing(file_id, org_id, error=err)
                    outcome, message = "failed", f"Processing failed: {err[:200]}"
                    logger.error(
                        f"Auto-process job {terminal['status']}: file={file_id}, "
                        f"tool={tool_name}, job={job_id}, error={err}"
                    )
            elif result.success:
                # Synchronous tool that actually finished.
                await self._file_client.complete_processing(
                    file_id, org_id,
                    results=data if data else {"result": str(result.data)},
                )
                outcome, message = "completed", (
                    f"Your file has been processed successfully by {tool_name}."
                )
                logger.info(f"Auto-process complete: file={file_id}, tool={tool_name}")
            else:
                await self._file_client.fail_processing(
                    file_id, org_id, error=result.error or "Processing failed",
                )
                outcome = "failed"
                message = f"Processing failed: {(result.error or '')[:200]}"
                logger.error(
                    f"Auto-process failed: file={file_id}, tool={tool_name}, "
                    f"error={result.error}"
                )

            # Single email-notification path for every outcome.
            try:
                user_email = user_context.get("user_email") or user_context.get("email", "")
                if user_email and self.dashboard_url:
                    await self.client.post(
                        f"{self.dashboard_url}/api/v1/notifications/file-complete",
                        json={
                            "email": user_email,
                            "file_id": file_id,
                            "tool_name": tool_name,
                            "status": outcome,
                            "message": message,
                            "dashboard_url": "https://app.novomcp.com/files",
                        },
                        headers={"X-Admin-Key": self.dashboard_admin_key},
                        timeout=10.0,
                    )
            except Exception as e:
                logger.warning(f"Failed to send {outcome} email for {file_id}: {e}")

        except Exception as e:
            logger.exception(f"Auto-process dispatcher error for {file_id}: {e}")
            try:
                await self._file_client.fail_processing(
                    file_id, org_id,
                    error=f"Dispatcher error: {str(e)[:300]}",
                )
            except Exception:
                pass

    async def _await_job_terminal(
        self,
        job_id: str,
        user_context: Dict[str, Any],
        timeout_s: int = 1800,
        interval_s: int = 20,
    ) -> Dict[str, Any]:
        """Poll get_job_status until an async job reaches a terminal state.

        Used by the auto-process dispatcher so an uploaded file's status mirrors
        the downstream job outcome instead of being finalized at submission time.
        Returns {"status": "completed", "result": {...}} or
        {"status": "failed"|"timeout", "error": "..."}. Runs inside the detached
        dispatcher task, so blocking for minutes here is fine.
        """
        import asyncio
        import time as _time

        deadline = _time.monotonic() + timeout_s
        while _time.monotonic() < deadline:
            try:
                st = await self._execute_get_job_status(
                    {"job_id": job_id}, context=user_context
                )
                data = st.data if (st and isinstance(st.data, dict)) else {}
                status = str(data.get("status") or "").lower()
                if status in ("completed", "succeeded", "success"):
                    return {"status": "completed",
                            "result": data.get("results") or data.get("result") or data}
                if status in ("failed", "error", "cancelled", "canceled"):
                    err = (data.get("error")
                           or (data.get("results") or {}).get("error")
                           or "Job failed")
                    return {"status": "failed", "error": err}
            except Exception as e:  # transient poll error — keep trying
                logger.warning(f"_await_job_terminal poll error for {job_id}: {e}")
            await asyncio.sleep(interval_s)

        return {
            "status": "timeout",
            "error": f"Job {job_id} did not reach a terminal state within "
                     f"{timeout_s // 60} min",
        }

    async def _execute_list_jobs(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """List async pipeline jobs from dashboard-aggregator."""
        status = args.get("status")
        service = args.get("service")
        limit = min(int(args.get("limit", 50)), 100)

        try:
            params = {"limit": str(limit)}
            if status:
                params["status"] = status
            if service:
                params["service"] = service

            response = await self.client.get(
                f"{self.dashboard_url}/api/v1/jobs",
                params=params,
                headers={"X-Admin-Key": self.dashboard_admin_key},
                timeout=15.0,
            )

            if response.status_code != 200:
                return ToolResult(
                    success=False,
                    error=f"Failed to list jobs: HTTP {response.status_code}",
                    usage={"queries": 0, "tool": "list_jobs"}
                )

            data = response.json()
            jobs = data.get("jobs", data if isinstance(data, list) else [])

            return ToolResult(
                success=True,
                data={
                    "jobs": jobs,
                    "total": data.get("total", len(jobs)),
                    "filters": {"status": status, "service": service, "limit": limit},
                },
                usage={"queries": 0, "tool": "list_jobs"}
            )

        except Exception as e:
            logger.error(f"Failed to list jobs: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to list jobs: {str(e)}",
                usage={"queries": 0, "tool": "list_jobs"}
            )

    async def _execute_get_pipeline_audit(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Retrieve per-molecule audit log for a pipeline execution."""
        pipeline_id = args.get("pipeline_id")
        if not pipeline_id:
            return ToolResult(success=False, error="pipeline_id is required")

        try:
            response = await self.client.get(
                f"{self.dashboard_url}/api/v1/pipelines/{pipeline_id}/audit",
                headers={"X-Admin-Key": self.dashboard_admin_key},
                timeout=15.0,
            )

            if response.status_code == 404:
                return ToolResult(
                    success=False,
                    error=f"Pipeline {pipeline_id} not found",
                    usage={"queries": 0, "tool": "get_pipeline_audit"}
                )

            if response.status_code != 200:
                return ToolResult(
                    success=False,
                    error=f"Failed to get audit log: HTTP {response.status_code}",
                    usage={"queries": 0, "tool": "get_pipeline_audit"}
                )

            data = response.json()

            return ToolResult(
                success=True,
                data=data,
                usage={"queries": 0, "tool": "get_pipeline_audit"}
            )

        except Exception as e:
            logger.error(f"Failed to get pipeline audit: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to get pipeline audit: {str(e)}",
                usage={"queries": 0, "tool": "get_pipeline_audit"}
            )

    async def _execute_save_funnel_stage(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Save a single event entry to the funnel audit log."""
        funnel_id = args.get("funnel_id") or (context or {}).get("funnel_id")
        if not funnel_id:
            return ToolResult(success=False, error="funnel_id is required (pass explicitly or via session)")
        if not self.dashboard_url:
            # Local mode: dashboard-aggregator unwired. Raw tool calls are still
            # audited via the local audit sink (~/.novo/audit.jsonl by default).
            return ToolResult(
                success=False,
                error="save_funnel_stage requires a funnel-persistence backend (set FUNNEL_BACKEND_URL, aliased from DASHBOARD_AGGREGATOR_URL for backwards compat). In local mode, raw tool calls are still audited via the local audit sink — see NOVO_AUDIT_PATH.",
                usage={"queries": 0, "tool": "save_funnel_stage"},
            )

        try:
            payload = {**args}
            payload["funnel_id"] = funnel_id
            payload.setdefault("event_type", "checkpoint")

            # Dual-write event_type into system_metadata (a confirmed JSON column in
            # funnel_audit_log) so the field survives even if the aggregator strips
            # unknown top-level keys. get_funnel_audit reads from either location.
            sysmeta = dict(payload.get("system_metadata") or {})
            sysmeta.setdefault("event_type", payload["event_type"])

            # Same dual-write for surface (chrome-ext-v1, word-addin-v1, ""):
            # priority is explicit args > caller context > absent. Lets us slice
            # funnel_audit_log by surface for analytics without a schema migration.
            surface_value = payload.get("surface") or (context or {}).get("surface") or ""
            if surface_value:
                payload["surface"] = surface_value
                sysmeta.setdefault("surface", surface_value)

            # Dual-write for client tag (claude-code, cursor, hex.tech-mcp, ...).
            # Stays in system_metadata only — no top-level column today, no
            # schema migration. Renders in the dashboard's audit row as the
            # secondary line under the surface chip via the resolveClient()
            # lookup in components/funnel/surfaceLabels.ts.
            client_value = payload.get("client") or (context or {}).get("client") or ""
            if client_value:
                sysmeta.setdefault("client", client_value)

            payload["system_metadata"] = sysmeta

            # Default stage_name to tool_name, stage_label to titleized stage_name
            if not payload.get("stage_name"):
                payload["stage_name"] = payload.get("tool_name") or "unknown"
            if not payload.get("stage_label"):
                payload["stage_label"] = str(payload["stage_name"]).replace("_", " ").title()

            # Auto-assign monotonic stage_index per funnel via Redis INCR; fall back to wall time
            if payload.get("stage_index") is None:
                stage_index = None
                r = await self._get_redis()
                if r:
                    try:
                        stage_index = await r.incr(f"{self._redis_prefix}:funnel:{funnel_id}:counter")
                        await r.expire(f"{self._redis_prefix}:funnel:{funnel_id}:counter", 7 * 24 * 3600)
                    except Exception as e:
                        logger.warning(f"Redis INCR failed for funnel {funnel_id}: {e}")
                if stage_index is None:
                    stage_index = int(time.time() * 1000)  # ms since epoch — monotonic fallback
                payload["stage_index"] = stage_index

            if context:
                payload["org_id"] = context.get("org_id")
                payload["user_id"] = context.get("user_id")

            response = await self.client.post(
                f"{self.dashboard_url}/api/v1/funnel/{funnel_id}/log",
                json=payload,
                headers={"X-Admin-Key": self.dashboard_admin_key},
                timeout=15.0,
            )

            if response.status_code != 200:
                body_snippet = ""
                try:
                    body_snippet = f" — {response.text[:300]}"
                except Exception:
                    pass
                return ToolResult(
                    success=False,
                    error=f"Failed to save funnel stage: HTTP {response.status_code}{body_snippet}",
                    usage={"queries": 0, "tool": "save_funnel_stage"}
                )

            result_data = response.json()
            result_data["_reminder"] = (
                "Note: raw tool calls are auto-logged server-side as exploration "
                "events under this funnel_id — you never need to call save_funnel_stage "
                "just to log a call, and you must NOT ask the user whether to log. "
                "Use save_funnel_stage only to record the user's explicit decision at "
                "an interactive checkpoint (human_reviewed: true)."
            )
            return ToolResult(
                success=True,
                data=result_data,
                usage={"queries": 0, "tool": "save_funnel_stage"}
            )
        except Exception as e:
            logger.error(f"Failed to save funnel stage: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to save funnel stage: {str(e)}",
                usage={"queries": 0, "tool": "save_funnel_stage"}
            )

    async def _execute_get_funnel_audit(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Retrieve funnel audit log, optionally filtered by event_type."""
        funnel_id = args.get("funnel_id") or (context or {}).get("funnel_id")
        if not funnel_id:
            return ToolResult(success=False, error="funnel_id is required")

        event_type = args.get("event_type", "all")

        try:
            params = {}
            if event_type and event_type != "all":
                params["event_type"] = event_type

            response = await self.client.get(
                f"{self.dashboard_url}/api/v1/funnel/{funnel_id}/log",
                params=params,
                headers={"X-Admin-Key": self.dashboard_admin_key},
                timeout=15.0,
            )

            if response.status_code == 404:
                return ToolResult(success=False, error=f"Funnel {funnel_id} not found")

            if response.status_code != 200:
                return ToolResult(
                    success=False,
                    error=f"Failed to get funnel audit: HTTP {response.status_code}",
                    usage={"queries": 0, "tool": "get_funnel_audit"}
                )

            data = response.json()

            # Client-side fallback filter. Belt-and-suspenders for older
            # dashboard-aggregator builds — the current backend now honors
            # the event_type query param on the /log endpoint and resolves
            # via COALESCE(event_type, system_metadata->>'event_type'), so
            # the response coming back is usually already filtered. This
            # block is a no-op then because the returned stages all match.
            #
            # Critical fix from 2026-06-09: the previous `or "checkpoint"`
            # default at the end of _stage_event_type caused every stage
            # with no event_type recorded ANYWHERE to silently match a
            # "checkpoint" filter. Result: the filter returned the full
            # trail unchanged for any funnel where the dual-write didn't
            # land on a few rows. Now treat "unknown" as a distinct value
            # so it never accidentally matches the requested filter.
            if event_type and event_type != "all" and isinstance(data, dict):
                stages = data.get("stages") or data.get("events") or data.get("log")
                if isinstance(stages, list):
                    def _stage_event_type(s: Dict[str, Any]) -> Optional[str]:
                        top = s.get("event_type")
                        if top:
                            return top
                        sysmeta = s.get("system_metadata") or {}
                        if isinstance(sysmeta, str):
                            try:
                                sysmeta = json.loads(sysmeta)
                            except Exception:
                                sysmeta = {}
                        if isinstance(sysmeta, dict):
                            return sysmeta.get("event_type")
                        return None

                    filtered = [s for s in stages if _stage_event_type(s) == event_type]
                    key = "stages" if "stages" in data else ("events" if "events" in data else "log")
                    data = {**data, key: filtered, "event_type_filter": event_type}

            return ToolResult(
                success=True,
                data=data,
                usage={"queries": 0, "tool": "get_funnel_audit"}
            )
        except Exception as e:
            logger.error(f"Failed to get funnel audit: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to get funnel audit: {str(e)}",
                usage={"queries": 0, "tool": "get_funnel_audit"}
            )

    async def _generate_memory_embedding(self, text: str) -> Optional[list]:
        """Generate an embedding for funnel_memory.summary_embedding.

        Dispatches on EMBEDDING_PROVIDER env var:
          * azure  → text-embedding-3-large dim=1536 (matryoshka truncation)
          * cohere → embed-english-v3.0 dim=1024

        The funnel_memory.summary_embedding column type must match — see
        migration 019 for the VECTOR(1536) → VECTOR(1024) ALTER that pairs
        with EMBEDDING_PROVIDER=cohere. Best-effort: returns None on any
        failure; memory row is still written without the embedding and a
        backfill can populate it later.
        """
        if not text or not isinstance(text, str):
            return None
        provider = os.getenv("EMBEDDING_PROVIDER", "azure").lower()
        if provider == "cohere":
            return await self._generate_memory_embedding_cohere(text)
        return await self._generate_memory_embedding_azure(text)

    async def _generate_memory_embedding_azure(self, text: str) -> Optional[list]:
        try:
            import aiohttp
            azure_key = os.getenv("AZURE_OPENAI_API_KEY", "")
            azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
            if not azure_key or not azure_endpoint:
                logger.warning(
                    "funnel_memory embedding skipped: AZURE_OPENAI_API_KEY or "
                    "AZURE_OPENAI_ENDPOINT env vars not set"
                )
                return None
            deployment = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-3-large")
            api_version = os.getenv("AZURE_OPENAI_EMBEDDING_API_VERSION", "2024-12-01-preview")
            url = f"{azure_endpoint}/openai/deployments/{deployment}/embeddings?api-version={api_version}"
            payload = {"input": text[:30000], "dimensions": 1536}
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers={"api-key": azure_key, "Content-Type": "application/json"},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        body = ""
                        try:
                            body = (await resp.text())[:400]
                        except Exception:
                            pass
                        logger.warning(
                            f"funnel_memory embedding failed: Azure OpenAI returned "
                            f"{resp.status} from deployment '{deployment}' "
                            f"(api-version={api_version}). Body: {body}"
                        )
                        return None
                    data = await resp.json()
                    emb = data.get("data", [{}])[0].get("embedding")
                    if isinstance(emb, list) and len(emb) == 1536:
                        return emb
                    actual_dim = len(emb) if isinstance(emb, list) else None
                    logger.warning(
                        f"funnel_memory embedding had unexpected shape: "
                        f"expected 1536-dim list, got dim={actual_dim}. "
                        f"Verify deployment '{deployment}' is a text-embedding-3-* "
                        f"model (matryoshka truncation is not supported on ada-002)."
                    )
                    return None
        except Exception as e:
            logger.warning(f"funnel_memory Azure embedding failed: {type(e).__name__}: {e}")
            return None

    async def _generate_memory_embedding_cohere(self, text: str) -> Optional[list]:
        try:
            import aiohttp
            cohere_key = os.getenv("COHERE_API_KEY", "")
            if not cohere_key:
                logger.warning(
                    "funnel_memory embedding skipped: COHERE_API_KEY env var not set"
                )
                return None
            url = "https://api.cohere.com/v2/embed"
            payload = {
                "model": os.getenv("COHERE_EMBED_MODEL", "embed-english-v3.0"),
                "texts": [text[:8000]],
                # search_document — memory rows are documents being indexed
                "input_type": "search_document",
                "embedding_types": ["float"],
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers={"Authorization": f"Bearer {cohere_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        body = ""
                        try:
                            body = (await resp.text())[:400]
                        except Exception:
                            pass
                        logger.warning(
                            f"funnel_memory Cohere embedding failed: HTTP {resp.status}. Body: {body}"
                        )
                        return None
                    data = await resp.json()
                    embs = data.get("embeddings", {}).get("float", [])
                    if embs and isinstance(embs[0], list) and len(embs[0]) == 1024:
                        return embs[0]
                    actual_dim = len(embs[0]) if embs and isinstance(embs[0], list) else None
                    logger.warning(
                        f"funnel_memory Cohere embedding had unexpected shape: "
                        f"expected 1024-dim list, got dim={actual_dim}"
                    )
                    return None
        except Exception as e:
            logger.warning(f"funnel_memory Cohere embedding failed: {type(e).__name__}: {e}")
            return None

    async def _execute_run_novo_ag(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Trigger tool: returns the full autonomous (or interactive) discovery_funnel prompt text.

        Bridges natural-language user intent ("Novo AG for AML", "agm glioblastoma")
        to the formal MCP prompt. The LLM calls this, receives the 200-line instruction
        set as its tool output, then follows the instructions step by step.

        Setup-guide fallback: if the compute stack isn't wired, return a short
        message listing missing env vars + doc links instead of the full protocol.
        This keeps agm discoverable without producing a "here's what would run"
        tour when nothing can actually execute.
        """
        disease = args.get("disease")
        if not disease:
            return ToolResult(
                success=False,
                error="Missing required parameter: disease (e.g., 'acute myeloid leukemia')"
            )

        mode = (args.get("mode") or "autonomous").lower()
        if mode not in ("autonomous", "interactive"):
            mode = "autonomous"

        md_duration_ns = args.get("md_duration_ns", 1)

        # Setup-guide fallback: check the minimum stack required to actually
        # run the funnel. If any of the essential env vars are missing, return
        # a setup-guide payload instead of the full protocol.
        _required_env = [
            ("NOVOMCP_DB_HOST",
             "PostgreSQL with the omics schema (target_discovery, validate_target, stratify_patients)",
             "docs/optional-data-services.md#omics"),
            ("ADDIE_MODELS_URL",
             "ADMET prediction service (predict_admet)",
             "docs/deploying-services/README.md"),
            ("AUTODOCK_GPU_URL",
             "AutoDock-GPU service for docking (dock_molecules, dock_with_strain)",
             "docs/deploying-services/README.md"),
            ("GROMACS_MD_URL",
             "GROMACS MD service for molecular dynamics (run_molecular_dynamics)",
             "docs/deploying-services/README.md"),
        ]
        _missing = [(name, why, doc) for name, why, doc in _required_env if not os.getenv(name, "").strip()]

        if _missing:
            return ToolResult(
                success=True,
                data={
                    "mode": mode,
                    "disease": disease,
                    "status": "setup_required",
                    "message": (
                        f"Autonomous discovery mode ({mode}) needs a compute stack that isn't fully "
                        f"wired on this install. Once these services are configured, agm will run a "
                        f"full 11-stage funnel for '{disease}'. Until then, use the always-available "
                        f"tools (search_chembl, search_clinical_trials, search_biorxiv, "
                        f"get_molecule_profile, batch_profile, screen_library) to research this "
                        f"target manually."
                    ),
                    "missing_services": [
                        {"env_var": name, "provides": why, "docs": doc}
                        for name, why, doc in _missing
                    ],
                    "quick_start": (
                        "For a hosted evaluation, sign up at https://novomcp.com (managed compute "
                        "stack). For self-hosted, see the deployment guides linked above — Modal, "
                        "Runpod, and self-hosted k8s walkthroughs cover every service in this list."
                    ),
                    "manual_workflow_hint": (
                        "In the meantime you can still run a research workflow manually: "
                        f"(1) search_clinical_trials for '{disease}' to see active drug programs; "
                        "(2) search_chembl by the top target gene to find known actives; "
                        "(3) get_molecule_profile on each candidate for drug-likeness. "
                        "That gives you a research picture without needing docking/MD/ADMET services."
                    ),
                },
                usage={"queries": 0, "tool": "run_novo_ag"}
            )

        template_key = "discovery_funnel" if mode == "autonomous" else "discovery_funnel_interactive"
        template = MCP_PROMPT_TEMPLATES.get(template_key)
        if not template:
            return ToolResult(success=False, error=f"Prompt template '{template_key}' not found")

        try:
            raw_text = template["messages"][0]["content"]["text"]
            # Substitute the same placeholders the MCP prompts/get endpoint uses
            instructions = raw_text.replace("{disease}", disease).replace(
                "{md_duration_ns}", str(md_duration_ns)
            )

            return ToolResult(
                success=True,
                data={
                    "mode": mode,
                    "disease": disease,
                    "md_duration_ns": md_duration_ns,
                    "instructions": instructions,
                    "next_step": (
                        "Follow the instructions above step by step. Generate funnel_id "
                        "FIRST (before any tool call), then pass it as an argument to every "
                        "tool call in this funnel. Call save_funnel_memory at the end to "
                        "seed cross-run learning."
                    ),
                },
                usage={"queries": 1, "tool": "run_novo_ag"}
            )
        except Exception as e:
            logger.error(f"Failed to load prompt template: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to load autonomous prompt: {str(e)}",
                usage={"queries": 0, "tool": "run_novo_ag"}
            )

    async def _execute_list_funnels(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """List recent discovery funnels with metadata from funnel_memory."""
        limit = args.get("limit", 15)
        target_gene = args.get("target_gene")
        outcome = args.get("outcome")

        try:
            params: Dict[str, Any] = {"limit": limit}
            if target_gene:
                params["target_gene"] = target_gene
            if outcome:
                params["outcome"] = outcome

            qs = "&".join(f"{k}={v}" for k, v in params.items())
            response = await self.client.get(
                f"{self.dashboard_url}/api/v1/funnel?{qs}",
                headers={"X-Admin-Key": self.dashboard_admin_key},
                timeout=15.0,
            )

            if response.status_code != 200:
                return ToolResult(
                    success=False,
                    error=f"Failed to list funnels: {response.status_code}",
                    usage={"queries": 0, "tool": "list_funnels"},
                )

            data = response.json()
            funnels = data.get("funnels", [])

            # Build tool suggestions based on results
            suggestions = []
            if funnels:
                suggestions.append(
                    self._tool_suggestion(
                        "get_funnel_audit",
                        f"View audit trail for {funnels[0].get('funnel_id', 'most recent funnel')}",
                    )
                )

            return ToolResult(
                success=True,
                data={
                    "funnels": funnels,
                    "total": data.get("total", len(funnels)),
                    "tool_suggestions": suggestions,
                },
                usage={"queries": 0, "tool": "list_funnels"},
            )

        except Exception as e:
            logger.exception(f"Error in list_funnels: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to list funnels: {str(e)}",
                usage={"queries": 0, "tool": "list_funnels"},
            )

    async def _execute_save_funnel_memory(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Write a terminal summary of a completed funnel to funnel_memory.

        Routes through dashboard-aggregator (owns identity-db). Generates a
        1536-dim embedding client-side via Azure OpenAI; server writes the row with
        or without the embedding depending on what we send.
        """
        funnel_id = args.get("funnel_id") or (context or {}).get("funnel_id")
        outcome = args.get("outcome")
        summary = args.get("summary")
        org_id = (context or {}).get("org_id", "")

        if not funnel_id:
            return ToolResult(success=False, error="funnel_id is required")
        if not outcome:
            return ToolResult(success=False, error="outcome is required (SUCCEEDED, FAILED_*, or ABANDONED)")
        if not summary:
            return ToolResult(success=False, error="summary is required (natural-language text for semantic search)")
        if not org_id:
            return ToolResult(success=False, error="org_id missing from context — caller must be authenticated")

        try:
            # Best-effort embedding generation; None on failure — aggregator writes without it
            embedding = await self._generate_memory_embedding(summary)

            payload = {
                "funnel_id": funnel_id,
                "org_id": org_id,
                "outcome": outcome,
                "summary": summary,
                "target_gene": args.get("target_gene"),
                "target_pdb_id": args.get("target_pdb_id"),
                "therapeutic_area": args.get("therapeutic_area"),
                "chemotype": args.get("chemotype"),
                "final_lead_count": args.get("final_lead_count") or 0,
                "best_affinity_kcal": args.get("best_affinity_kcal"),
                "failure_pattern": args.get("failure_pattern"),
                "decisions": args.get("decisions"),
            }
            # Phase 0 perturbation evidence channel -- T1-D / W3.3.
            # Three nullable fields persisted to funnel_memory so the learning
            # loop can correlate funnel outcomes with whether the channel was
            # active, whether it shifted the top-3, and what coverage status it
            # reported (FM 13 surfaces here, by design -- not buried). Aggregator
            # will pass through nulls so pre-Phase-0 funnels keep working.
            pert_active = args.get("perturbation_channel_active")
            if pert_active is not None:
                payload["perturbation_channel_active"] = bool(pert_active)
            pert_changed = args.get("perturbation_changed_top3")
            if pert_changed is not None:
                payload["perturbation_changed_top3"] = bool(pert_changed)
            pert_cov = args.get("perturbation_channel_coverage")
            if pert_cov is not None:
                # Normalise to the locked domain; anything else gets recorded
                # as 'unknown' rather than being silently passed through. The
                # DB doesn't enforce -- we do.
                allowed = {"ok", "degraded_no_coverage", "disabled", "unknown"}
                payload["perturbation_channel_coverage"] = (
                    pert_cov if pert_cov in allowed else "unknown"
                )
            if embedding is not None:
                payload["summary_embedding"] = embedding

            response = await self.client.post(
                f"{self.dashboard_url}/api/v1/funnel/memory",
                json=payload,
                headers={"X-Admin-Key": self.dashboard_admin_key},
                timeout=20.0,
            )

            if response.status_code in (200, 201):
                data = response.json()
                return ToolResult(
                    success=True,
                    data={
                        "funnel_id": funnel_id,
                        "outcome": outcome,
                        "has_embedding": bool(data.get("has_embedding")),
                        "message": "Funnel memory saved. Use search_prior_runs to retrieve from future runs."
                    },
                    usage={"queries": 1, "tool": "save_funnel_memory"}
                )

            body_snippet = ""
            try:
                body_snippet = f" — {response.text[:300]}"
            except Exception:
                pass
            return ToolResult(
                success=False,
                error=f"Failed to save funnel memory: HTTP {response.status_code}{body_snippet}",
                usage={"queries": 0, "tool": "save_funnel_memory"}
            )

        except Exception as e:
            logger.error(f"Failed to save funnel memory for {funnel_id}: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to save funnel memory: {str(e)}",
                usage={"queries": 0, "tool": "save_funnel_memory"}
            )

    async def _execute_search_prior_runs(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Query funnel_memory for prior runs. Routes through dashboard-aggregator.

        When a natural-language query is provided and embeddings are available,
        uses POST /api/v1/funnel/memory/search (cosine VECTOR_DISTANCE). Otherwise
        uses GET /api/v1/funnel/memory with filters. The GET endpoint also runs
        the lazy backstop on every call — finds completed funnels missing memory
        entries and writes template summaries before returning results.
        """
        org_id = (context or {}).get("org_id", "")
        if not org_id:
            return ToolResult(success=False, error="org_id missing from context — caller must be authenticated")
        if not self.dashboard_url:
            return ToolResult(
                success=False,
                error="search_prior_runs requires a funnel-persistence backend (set FUNNEL_BACKEND_URL, aliased from DASHBOARD_AGGREGATOR_URL for backwards compat). Cross-run funnel memory is not wired in local mode.",
                usage={"queries": 0, "tool": "search_prior_runs"},
            )

        target_gene = args.get("target_gene")
        target_pdb_id = args.get("target_pdb_id")
        therapeutic_area = args.get("therapeutic_area")
        outcome = args.get("outcome", "any")
        query_text = args.get("query")
        max_results = min(int(args.get("max_results", 10)), 50)

        try:
            # Semantic search path: generate embedding client-side, POST to aggregator
            if query_text:
                query_emb = await self._generate_memory_embedding(query_text)
                if query_emb is not None:
                    payload = {
                        "org_id": org_id,
                        "query_embedding": query_emb,
                        "limit": max_results,
                    }
                    if target_gene:
                        payload["target_gene"] = target_gene
                    if target_pdb_id:
                        payload["target_pdb_id"] = target_pdb_id
                    if therapeutic_area:
                        payload["therapeutic_area"] = therapeutic_area
                    if outcome and outcome != "any":
                        payload["outcome"] = outcome

                    response = await self.client.post(
                        f"{self.dashboard_url}/api/v1/funnel/memory/search",
                        json=payload,
                        headers={"X-Admin-Key": self.dashboard_admin_key},
                        timeout=20.0,
                    )
                    if response.status_code == 200:
                        data = response.json()
                        return ToolResult(
                            success=True,
                            data={
                                "results": data.get("results", []),
                                "count": data.get("count", 0),
                                "backstop_writes": 0,  # semantic path doesn't run backstop (GET path does)
                                "semantic_search_used": True,
                            },
                            usage={"queries": 1, "tool": "search_prior_runs"}
                        )
                    # fall through to keyword GET path on semantic failure

            # Keyword / filter path: GET with backstop
            params = {
                "org_id": org_id,
                "limit": max_results,
                "run_backstop": "true",
            }
            if target_gene:
                params["target_gene"] = target_gene
            if target_pdb_id:
                params["target_pdb_id"] = target_pdb_id
            if therapeutic_area:
                params["therapeutic_area"] = therapeutic_area
            if outcome and outcome != "any":
                params["outcome"] = outcome

            response = await self.client.get(
                f"{self.dashboard_url}/api/v1/funnel/memory",
                params=params,
                headers={"X-Admin-Key": self.dashboard_admin_key},
                timeout=20.0,
            )

            if response.status_code != 200:
                body_snippet = ""
                try:
                    body_snippet = f" — {response.text[:300]}"
                except Exception:
                    pass
                return ToolResult(
                    success=False,
                    error=f"Failed to search prior runs: HTTP {response.status_code}{body_snippet}",
                    usage={"queries": 0, "tool": "search_prior_runs"}
                )

            data = response.json()
            # Optional client-side keyword filter on summary if a query_text was passed
            # but semantic search failed (embedding unavailable)
            results = data.get("results", [])
            if query_text:
                qlower = query_text.lower()
                results = [r for r in results if qlower in (r.get("summary") or "").lower()]

            return ToolResult(
                success=True,
                data={
                    "results": results,
                    "count": len(results),
                    "backstop_writes": data.get("backstop_writes", 0),
                    "semantic_search_used": False,
                },
                usage={"queries": 1, "tool": "search_prior_runs"}
            )

        except Exception as e:
            logger.error(f"Failed to search prior runs: {e}")
            return ToolResult(
                success=False,
                error=f"Failed to search prior runs: {str(e)}",
                usage={"queries": 0, "tool": "search_prior_runs"}
            )

    async def _execute_get_job_status(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """
        Universal job status checker for all async NovoMCP operations.

        Supports:
        - openfold3: Structure prediction
        - gromacs: Molecular dynamics (v4/v5)
        - novo-quantum: Quantum calculations (v4/v5)
        - lead-optimization: Lead optimization workflows (v4/v5)

        Multi-tenant safety (Phase 3.2): when `context` provides an org_id,
        downstream dashboard reads include `X-Org-Id` so cross-tenant access
        returns 404. Internal callers without context (e.g. get_structure_result
        fallback) continue to bypass the check — they've established trust
        higher up the stack.
        """
        caller_org_id = context.get("org_id") if context else None
        job_id = args.get("job_id")
        if not job_id:
            return ToolResult(success=False, error="Missing required parameter: job_id")

        service = args.get("service", "auto")

        # Auto-detect service from job_id prefix if not specified
        if service == "auto":
            if job_id.startswith("gro_"):
                service = "gromacs"
            elif job_id.startswith("dock_batch_"):
                service = "dock-batch"
            elif job_id.startswith("dock_"):
                service = "autodock-gpu"
            elif job_id.startswith("neb_"):
                service = "novomcp-neb"
            elif job_id.startswith("qm_"):
                service = "novomcp-qm"
            elif job_id.startswith("qc_"):
                service = "novo-quantum"
            elif job_id.startswith("lo_"):
                service = "lead-optimization"
            elif job_id.startswith("af_"):
                service = "alphaflow"
            elif job_id.startswith("of3_"):
                service = "openfold3"
            elif job_id.startswith("mcpb_"):
                service = "parameterize-metal"
            else:
                # Default to openfold3 for backward compatibility (UUID format)
                service = "openfold3"

        # ---- dock-batch jobs: read status from Redis (managed by novomcp background task) ----
        if service == "dock-batch":
            try:
                r = await self._get_redis()
                if not r:
                    return ToolResult(success=False, error="Redis unavailable — cannot check batch docking status")
                redis_key = f"{self._redis_prefix}:dock:{job_id}"
                job_data = await r.hgetall(redis_key)
                if not job_data:
                    # Redis key expired — try SQL fallback via dashboard-aggregator
                    try:
                        ctx_resp = await self.client.get(
                            f"{self.dashboard_url}/api/v1/jobs/{job_id}/context",
                            headers={"X-Admin-Key": self.dashboard_admin_key},
                            timeout=10.0,
                        )
                        if ctx_resp.status_code == 200:
                            ctx_data = ctx_resp.json()
                            result_data = ctx_data.get("result_data")
                            if result_data:
                                return ToolResult(success=True, data={
                                    "job_id": job_id,
                                    "service": "autodock-gpu",
                                    "status": "completed",
                                    "completed": True,
                                    "results": result_data,
                                    "note": "Retrieved from persistent storage (Redis cache expired)",
                                    "tool_suggestions": [
                                        self._tool_suggestion("run_molecular_dynamics", "Run MD simulation on top candidates"),
                                        self._tool_suggestion("stratify_patients", "Assess clinical viability"),
                                    ]
                                }, usage={"queries": 0, "tool": "get_job_status"})
                    except Exception:
                        pass
                    return ToolResult(success=False, error=f"Batch docking job {job_id} not found (Redis expired, no SQL backup)")
                # Decode bytes if needed
                job_data = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v) for k, v in job_data.items()}

                status = job_data.get("status", "unknown")
                total = int(job_data.get("total", 0))
                completed_count = int(job_data.get("completed_count", 0))
                progress = int(job_data.get("progress", 0))

                result = {
                    "job_id": job_id,
                    "service": "autodock-gpu",
                    "status": status,
                    "progress_percent": progress,
                    "molecules_completed": completed_count,
                    "molecules_total": total,
                }

                if status == "completed":
                    result["completed"] = True
                    result_json = job_data.get("result_json")
                    if result_json:
                        result["results"] = json.loads(result_json)
                    result["tool_suggestions"] = [
                        self._tool_suggestion("run_molecular_dynamics", "Run MD simulation on top candidates"),
                        self._tool_suggestion("stratify_patients", "Assess clinical viability of top candidate"),
                    ]
                elif status in ("failed", "error"):
                    result["completed"] = True
                    result["error"] = job_data.get("error", "Batch docking failed")
                else:
                    result["completed"] = False
                    remaining_mols = total - completed_count
                    est_remaining_min = max(1, round(remaining_mols * 0.6))  # ~35s per molecule
                    result["estimated_remaining_minutes"] = est_remaining_min
                    result["message"] = (
                        f"STILL RUNNING — this is expected, not an error. "
                        f"Docking in progress: {completed_count}/{total} molecules completed ({progress}%). "
                        f"~{est_remaining_min} minute(s) remaining. "
                        f"You MUST call get_job_status again in 30 seconds to check for completion."
                    )

                return ToolResult(success=True, data=result, usage={"queries": 0, "tool": "get_job_status"})
            except Exception as e:
                logger.exception(f"Error checking dock-batch job {job_id}: {e}")
                return ToolResult(success=False, error=f"Failed to check batch docking status: {e}")

        # ---- parameterize-metal jobs: read from Redis (managed by novomcp background task) ----
        if service == "parameterize-metal":
            try:
                r = await self._get_redis()
                if not r:
                    return ToolResult(success=False, error="Redis unavailable — cannot check parameterize_metal status")
                redis_key = f"{self._redis_prefix}:mcpb:{job_id}"
                job_data = await r.hgetall(redis_key)
                if not job_data:
                    # Redis key expired — try SQL fallback
                    try:
                        ctx_resp = await self.client.get(
                            f"{self.dashboard_url}/api/v1/jobs/{job_id}/context",
                            headers={"X-Admin-Key": self.dashboard_admin_key},
                            timeout=10.0,
                        )
                        if ctx_resp.status_code == 200:
                            ctx_data = ctx_resp.json()
                            result_data = ctx_data.get("result_data")
                            if result_data:
                                return ToolResult(success=True, data={
                                    "job_id": job_id,
                                    "service": "parameterize-metal",
                                    "status": "completed",
                                    "completed": True,
                                    "results": result_data,
                                    "note": "Retrieved from persistent storage (Redis cache expired)",
                                }, usage={"queries": 0, "tool": "get_job_status"})
                    except Exception:
                        pass
                    return ToolResult(success=False, error=f"parameterize_metal job {job_id} not found (Redis expired, no SQL backup)")
                # Decode bytes if needed
                job_data = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v) for k, v in job_data.items()}

                status = job_data.get("status", "unknown")
                phase = int(job_data.get("phase", 1))
                result = {
                    "job_id": job_id,
                    "service": "parameterize-metal",
                    "status": status,
                    "phase": phase,
                    "pdb_id": job_data.get("pdb_id"),
                    "metal_resid": job_data.get("metal_resid"),
                }

                if status == "completed":
                    result["completed"] = True
                    result_json = job_data.get("result_json")
                    if result_json:
                        result["results"] = json.loads(result_json)
                    # Suggest next step based on phase
                    if phase == 1:
                        result["tool_suggestions"] = [
                            self._tool_suggestion(
                                "parameterize_metal",
                                "Phase 2: upload Gaussian/ORCA log via qm_file_id + confirmation_token",
                            ),
                        ]
                    else:
                        result["tool_suggestions"] = [
                            self._tool_suggestion(
                                "run_molecular_dynamics",
                                "Run MD with parameterized metal site",
                            ),
                        ]
                elif status == "failed":
                    result["completed"] = True
                    result["error"] = job_data.get("error", "parameterize_metal failed")
                    result_json = job_data.get("result_json")
                    if result_json:
                        result["results"] = json.loads(result_json)
                else:
                    result["completed"] = False
                    result["message"] = (
                        f"STILL RUNNING — this is expected, not an error. "
                        f"parameterize_metal Phase {phase} in progress. "
                        f"Phase 1 typically completes in ~1-2 min, Phase 2 in ~2-5 min. "
                        f"Call get_job_status again in 60 seconds."
                    )

                return ToolResult(success=True, data=result, usage={"queries": 0, "tool": "get_job_status"})
            except Exception as e:
                logger.exception(f"Error checking parameterize-metal job {job_id}: {e}")
                return ToolResult(success=False, error=f"Failed to check parameterize_metal status: {e}")


        # ---- MD jobs (gro_*): SQL-first via dashboard-aggregator (durable) ----
        # gromacs-md-job is the dispatcher target, and novomcp pre-creates
        # the async_jobs SQL row at submission. The gromacs-md Container App
        # scales to zero, so the legacy /status HTTP path triggers a ~30-60s
        # cold start that often exceeds the MCP gateway timeout. Reading SQL
        # first avoids that path entirely on the happy case; the existing
        # container HTTP + Redis fallback below still covers SQL misses.
        if service == "gromacs":
            try:
                md_headers = {"X-Admin-Key": self.dashboard_admin_key}
                if caller_org_id:
                    md_headers["X-Org-Id"] = caller_org_id
                ctx_resp = await self.client.get(
                    f"{self.dashboard_url}/api/v1/jobs/{job_id}/context",
                    headers=md_headers,
                    timeout=10.0,
                )
                if ctx_resp.status_code == 200:
                    ctx = ctx_resp.json()
                    status = ctx.get("status", "unknown")

                    fc = ctx.get("funnel_context") or {}
                    if isinstance(fc, dict):
                        estimated_minutes = int(fc.get("estimated_minutes") or 15)
                    else:
                        estimated_minutes = 15
                    now = datetime.now(timezone.utc)

                    def _parse_iso(s):
                        if not s:
                            return None
                        try:
                            s = s.replace("Z", "+00:00")
                            dt = datetime.fromisoformat(s)
                            # SQL columns return naive ISO strings (no tz) for
                            # submitted_at/started_at. Default to UTC so the
                            # subsequent `now - dt` arithmetic doesn't blow up.
                            # _parse_iso copy; this MD copy was missed because
                            # of inter-commit drift.)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            return dt
                        except Exception:
                            return None

                    if status in ("queued", "submitted", "provisioning"):
                        submitted_at = _parse_iso(ctx.get("submitted_at"))
                        if submitted_at and (now - submitted_at).total_seconds() > 30 * 60:
                            # Check Redis before killing — executor writes to Redis,
                            redis_status = None
                            try:
                                r = await self._get_redis()
                                if r:
                                    redis_data = await r.hgetall(f"novomcp:job:{job_id}")
                                    redis_status = redis_data.get("status") if redis_data else None
                            except Exception:
                                pass
                            if redis_status and redis_status not in ("queued", "submitted", "provisioning"):
                                logger.info(
                                    f"Watchdog: MD {job_id} shows '{status}' in SQL but "
                                    f"'{redis_status}' in Redis — backfilling SQL, not killing"
                                )
                                status = redis_status
                            else:
                                await self._dashboard_patch_job(job_id, {
                                    "status": "failed",
                                    "progress_pct": 0,
                                    "progress_message": "Failed to start within 30 minutes",
                                    "error_message": (
                                        f"MD job did not transition out of {status} within 30 minutes "
                                        "of submission. The GPU node may be unavailable or the "
                                        "k8s Job execution failed silently. Check via "
                                        "az containerapp job execution show -n gromacs-md-job."
                                    ),
                                })
                                status = "failed"
                                ctx["error_message"] = "Job failed to start within 30 minutes."
                    elif status == "running":
                        started_at = _parse_iso(ctx.get("started_at"))
                        max_minutes = max(estimated_minutes * 2, 30)
                        progress_pct = ctx.get("progress_pct", 0)
                        if started_at and (now - started_at).total_seconds() > max_minutes * 60:
                            if progress_pct > 5:
                                logger.warning(
                                    f"Watchdog: {job_id} exceeded {max_minutes}min cap but "
                                    f"progress is {progress_pct}% — job is alive, not killing"
                                )
                            else:
                                await self._dashboard_patch_job(job_id, {
                                    "status": "failed",
                                    "progress_pct": progress_pct,
                                    "progress_message": f"Exceeded {max_minutes}min runtime cap",
                                    "error_message": (
                                        f"MD job exceeded {max_minutes} minutes (2× the {estimated_minutes}min "
                                        "estimate) with no progress (0%). Presumed dead."
                                    ),
                                })
                                status = "failed"
                                ctx["error_message"] = f"Exceeded maximum runtime ({max_minutes} min)."

                    result = {
                        "job_id": job_id,
                        "service": "gromacs",
                        "status": status,
                        "source": "sql",
                        "progress_percent": ctx.get("progress_pct", 0),
                        "progress_message": ctx.get("progress_message"),
                        "execution_id": ctx.get("execution_id"),
                    }

                    if status == "completed":
                        result["completed"] = True
                        result_data = ctx.get("result_data")
                        if not result_data:
                            try:
                                r = await self._get_redis()
                                if r:
                                    result_json = await r.get(f"{self._redis_prefix}:job_result:{job_id}")
                                    if result_json:
                                        result_data = json.loads(
                                            result_json.decode() if isinstance(result_json, bytes) else result_json
                                        )
                                        try:
                                            await self.client.patch(
                                                f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                                                json={"status": "completed", "progress_pct": 100, "result_data": result_data},
                                                headers={"X-Admin-Key": self.dashboard_admin_key},
                                                timeout=10.0,
                                            )
                                        except Exception:
                                            pass
                            except Exception as e:
                                logger.debug(f"Redis result fetch for MD {job_id}: {e}")
                        if result_data:
                            result["results"] = result_data
                        result["tool_suggestions"] = [
                            self._tool_suggestion(
                                "stratify_patients",
                                "Assess clinical viability via pharmacogenomic analysis after MD validation"
                            ),
                        ]
                    elif status == "failed":
                        result["completed"] = True
                        result["error"] = ctx.get("error_message") or "MD simulation failed"
                    elif status == "cancelled":
                        result["completed"] = True
                        result["cancelled"] = True
                        result["message"] = (
                            "MD job was cancelled. Partial stage checkpoints may be available in "
                            "md-checkpoints/{job_id}/; resubmitting with the same inputs will "
                            "resume from the last completed stage (system_prep / em / nvt / npt / "
                            "production)."
                        )
                    elif status == "provisioning":
                        result["completed"] = False
                        result["message"] = (
                            f"MD job is provisioning ({result['progress_percent']}%) — "
                            f"EKS is provisioning the GPU node and pulling the container image. "
                            f"Typically 2-5 minutes for the first run, faster on warm nodes. "
                            f"{ctx.get('progress_message') or ''} "
                            f"Poll again in 60 seconds. Estimated total runtime: ~{estimated_minutes} min."
                        )
                    else:
                        # queued, running, or unknown — STILL RUNNING per SQL.
                        # But the executor writes Redis only, and novomcp
                        # has no background worker that bridges Redis → SQL.
                        # So when the executor finishes, SQL stays stale on
                        # the submission status until *someone* triggers a
                        # PATCH. Make that someone us: check Redis here and,
                        # if Redis has a terminal status, patch SQL and
                        # return the terminal result. Otherwise the user
                        # sees "queued" forever even after the job succeeded.
                        bridged = False
                        try:
                            r = await self._get_redis()
                            if r:
                                redis_data = await r.hgetall(f"{self._redis_prefix}:job:{job_id}")
                                if redis_data:
                                    redis_data = {
                                        (k.decode() if isinstance(k, bytes) else k):
                                        (v.decode() if isinstance(v, bytes) else v)
                                        for k, v in redis_data.items()
                                    }
                                    redis_status = redis_data.get("status")
                                    if redis_status in ("completed", "failed", "cancelled"):
                                        # Bridge Redis terminal state to SQL.
                                        result_data = None
                                        if redis_status == "completed":
                                            result_json = redis_data.get("result")
                                            if not result_json:
                                                result_json = await r.get(
                                                    f"{self._redis_prefix}:job_result:{job_id}"
                                                )
                                                if isinstance(result_json, bytes):
                                                    result_json = result_json.decode()
                                            if result_json:
                                                try:
                                                    result_data = json.loads(result_json)
                                                except Exception:
                                                    result_data = None
                                        patch_payload = {
                                            "status": redis_status,
                                            "progress_pct": 100 if redis_status == "completed" else (
                                                ctx.get("progress_pct", 0)
                                            ),
                                        }
                                        if redis_status == "completed" and result_data:
                                            patch_payload["result_data"] = result_data
                                        if redis_status == "failed":
                                            patch_payload["error_message"] = redis_data.get("error", "")[:2000]
                                        try:
                                            await self.client.patch(
                                                f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                                                json=patch_payload,
                                                headers={"X-Admin-Key": self.dashboard_admin_key},
                                                timeout=10.0,
                                            )
                                        except Exception as e:
                                            logger.debug(f"SQL bridge PATCH failed for {job_id}: {e}")
                                        # Rebuild result with Redis terminal state
                                        result = {
                                            "job_id": job_id,
                                            "service": "gromacs",
                                            "status": redis_status,
                                            "source": "sql+redis_bridge",
                                            "progress_percent": patch_payload["progress_pct"],
                                            "execution_id": ctx.get("execution_id"),
                                        }
                                        if redis_status == "completed":
                                            result["completed"] = True
                                            if result_data:
                                                result["results"] = result_data
                                            result["tool_suggestions"] = [
                                                self._tool_suggestion(
                                                    "stratify_patients",
                                                    "Assess clinical viability via pharmacogenomic analysis after MD validation"
                                                ),
                                            ]
                                        elif redis_status == "failed":
                                            result["completed"] = True
                                            result["error"] = redis_data.get("error") or "MD simulation failed"
                                        else:  # cancelled
                                            result["completed"] = True
                                            result["cancelled"] = True
                                        bridged = True
                        except Exception as e:
                            logger.debug(f"Redis bridge check for MD {job_id} failed: {e}")

                        if not bridged:
                            result["completed"] = False
                            result["message"] = (
                                f"MD job is {status} ({result['progress_percent']}%). "
                                f"{ctx.get('progress_message') or ''} "
                                f"— STILL RUNNING — this is expected, not an error. "
                                f"Stages run sequentially: system_prep → em → nvt → npt → production → "
                                f"analysis. Production dominates for ≥10ns runs. Poll again in 60 seconds."
                            )

                    return ToolResult(success=True, data=result, usage={"queries": 0, "tool": "get_job_status"})
            except Exception as e:
                logger.warning(f"Dashboard SQL read failed for MD job {job_id}: {e} — falling back to container/Redis")
                # Fall through to container HTTP → Redis fallback below

        # Service endpoint mapping
        service_endpoints = {
            "openfold3": ("openfold3", f"/result/{job_id}"),
            "gromacs": ("gromacs-md", f"/status/{job_id}"),
            "novomcp-qm": ("novomcp-qm", f"/status/{job_id}"),
            "novomcp-neb": ("novomcp-neb", f"/status/{job_id}"),
            "novo-quantum": ("novo-quantum", f"/jobs/{job_id}/status"),
            "lead-optimization": ("lead-optimization", f"/jobs/{job_id}/status"),
            "autodock-gpu": ("autodock-gpu", f"/jobs/{job_id}/status"),
            "alphaflow": ("alphaflow", f"/status/{job_id}"),
        }

        if service not in service_endpoints:
            return ToolResult(success=False, error=f"Unknown service: {service}")

        svc_name, endpoint = service_endpoints[service]

        try:
            response = await self._call_service(svc_name, endpoint, {}, method="GET", timeout=30.0)

            if response.status_code == 200:
                data = response.json()
                # Unwrap service envelope if present (gromacs-md wraps in {service, status, data})
                if "data" in data and isinstance(data["data"], dict):
                    data = data["data"]
                status = data.get("status", "unknown")

                result = {
                    "job_id": job_id,
                    "service": service,
                    "status": status,
                }

                # Include progress if available
                progress = data.get("progress", {})
                if isinstance(progress, dict):
                    result["progress_percent"] = progress.get("percentage", 0)
                elif isinstance(progress, (int, float)):
                    result["progress_percent"] = progress
                if "eta" in data or "estimated_remaining" in data:
                    result["estimated_remaining"] = data.get("eta") or data.get("estimated_remaining")

                # Include results if completed
                if status == "completed":
                    result["completed"] = True
                    # Include relevant result data based on service
                    if service == "openfold3":
                        result["structure"] = data.get("structure")
                        result["format"] = data.get("format", "pdb")
                        result["confidence_scores"] = data.get("confidence_scores")
                    elif service == "gromacs":
                        # Fetch full results from /results endpoint
                        try:
                            result_resp = await self._call_service(
                                "gromacs-md", f"/results/{job_id}", {},
                                method="GET", timeout=15.0,
                            )
                            if result_resp.status_code == 200:
                                result_data = result_resp.json()
                                if "data" in result_data and isinstance(result_data["data"], dict):
                                    result_data = result_data["data"]
                                result["results"] = result_data.get("result", result_data)
                        except Exception as e:
                            logger.warning(f"Failed to fetch MD results for {job_id}: {e}")
                            result["results"] = data.get("results") or data.get("result")
                    elif service == "novomcp-qm":
                        # Fetch full results from /results endpoint
                        try:
                            result_resp = await self._call_service(
                                "novomcp-qm", f"/results/{job_id}", {},
                                method="GET", timeout=15.0,
                            )
                            if result_resp.status_code == 200:
                                result_data = result_resp.json()
                                result["results"] = result_data.get("result", result_data)
                        except Exception as e:
                            logger.warning(f"Failed to fetch QM results for {job_id}: {e}")
                            result["results"] = data.get("results") or data.get("result")
                    elif service == "novomcp-neb":
                        # Fetch NEB transition state results
                        try:
                            result_resp = await self._call_service(
                                "novomcp-neb", f"/results/{job_id}", {},
                                method="GET", timeout=15.0,
                            )
                            if result_resp.status_code == 200:
                                result_data = result_resp.json()
                                result["results"] = result_data.get("result", result_data)
                        except Exception as e:
                            logger.warning(f"Failed to fetch NEB results for {job_id}: {e}")
                            result["results"] = data.get("results") or data.get("result")
                    elif service == "alphaflow":
                        # Fetch full results (multi-model PDB, RMSF) from /results endpoint
                        try:
                            result_resp = await self._call_service(
                                "alphaflow", f"/results/{job_id}", {},
                                method="GET", timeout=15.0,
                            )
                            if result_resp.status_code == 200:
                                result_data = result_resp.json()
                                result["results"] = result_data.get("result", result_data)
                        except Exception as e:
                            logger.warning(f"Failed to fetch AlphaFlow results for {job_id}: {e}")
                            result["results"] = data.get("results") or data.get("result")

                        # Slim the pdb_ensemble blob by default. A completed
                        # AlphaFlow job carries a multi-model PDB inline (~130KB
                        # for a 50-frame run on a 50-residue protein) which blows
                        # past the 100KB-ish soft limit for inline MCP tool
                        # results on most clients. Keep analysis (rmsf, pca,
                        # scores) intact and replace only the byte-blob field
                        # with metadata + an opt-in pointer. Callers that need
                        # the full ensemble pass include_ensemble=true.
                        if (
                            isinstance(result.get("results"), dict)
                            and not args.get("include_ensemble", False)
                        ):
                            ensemble = result["results"].get("pdb_ensemble")
                            if isinstance(ensemble, str) and len(ensemble) > 8000:
                                frame_count = ensemble.count("MODEL ")
                                result["results"]["pdb_ensemble"] = {
                                    "_slimmed": True,
                                    "size_bytes": len(ensemble),
                                    "frame_count": frame_count,
                                    "preview": ensemble[:600] + "…",
                                    "hint": (
                                        "Inline ensemble is omitted by default to keep "
                                        "tool responses under the MCP inline-size limit. "
                                        "Pass include_ensemble=true to get the full multi-"
                                        "model PDB back. The Apps viewer renders it from "
                                        "the alphaflow service directly via /results/"
                                        + str(job_id) + "."
                                    ),
                                }
                    else:
                        result["results"] = data.get("results") or data.get("data")

                    # Update dashboard-aggregator for trackable jobs
                    if service in ("gromacs", "novomcp-qm", "novomcp-neb", "alphaflow"):
                        try:
                            patch_payload = {"status": "completed", "progress_pct": 100}
                            await self.client.patch(
                                f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                                json=patch_payload,
                                headers={"X-Admin-Key": self.dashboard_admin_key},
                                timeout=10.0,
                            )
                        except Exception:
                            pass  # Best-effort

                    # Trigger notification for completed gromacs MD jobs
                    if service == "gromacs" and job_id.startswith("gro_"):
                        try:
                            await self.client.post(
                                f"{self.dashboard_url}/api/v1/jobs/{job_id}/notify",
                                headers={"X-Admin-Key": self.dashboard_admin_key},
                                timeout=10.0,
                            )
                        except Exception:
                            pass  # Best-effort — don't fail the status check

                elif status == "refused":
                    # Terminal state: intake classifier rejected the system (membrane
                    # protein, metalloprotein, heme cofactor, Fe-S cluster, etc.).
                    # This is a successful classification outcome, NOT an error — the
                    # LLM must treat it like a completed job with a specific explanation.
                    result["completed"] = True
                    result["refused"] = True

                    # Fetch the structured refusal payload from /results
                    if service == "gromacs":
                        try:
                            result_resp = await self._call_service(
                                "gromacs-md", f"/results/{job_id}", {},
                                method="GET", timeout=15.0,
                            )
                            if result_resp.status_code == 200:
                                result_data = result_resp.json()
                                if "data" in result_data and isinstance(result_data["data"], dict):
                                    result_data = result_data["data"]
                                refusal = result_data.get("result", {}) or {}
                                result["primary_reason"] = refusal.get("primary_reason", "system unsupported")
                                result["reasons"] = refusal.get("reasons", [])
                                result["suggested_branch"] = refusal.get("suggested_branch")
                                result["profile"] = refusal.get("profile", {})
                        except Exception as e:
                            logger.warning(f"Failed to fetch refusal details for {job_id}: {e}")

                    # Build user-facing message calibrated for LLM consumption
                    reason = result.get("primary_reason", "system outside current pipeline scope")
                    suggested = result.get("suggested_branch")
                    suggested_hint = f" Future branch: {suggested}." if suggested else ""
                    result["message"] = (
                        f"REFUSED (terminal, do not retry): {reason}.{suggested_hint} "
                        f"This is a successful classification outcome, not an error. "
                        f"Explain the specific reason to the user and note the target "
                        f"is outside the current pipeline's supported scope. Do NOT "
                        f"poll again or retry."
                    )

                    # Update dashboard-aggregator so the jobs UI shows refused, not stuck
                    if service in ("gromacs", "novomcp-qm", "novomcp-neb", "alphaflow"):
                        try:
                            await self.client.patch(
                                f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                                json={
                                    "status": "refused",
                                    "progress_pct": 100,
                                    "result_data": {
                                        "primary_reason": result.get("primary_reason"),
                                        "reasons": result.get("reasons"),
                                        "suggested_branch": result.get("suggested_branch"),
                                        "profile": result.get("profile"),
                                    },
                                },
                                headers={"X-Admin-Key": self.dashboard_admin_key},
                                timeout=10.0,
                            )
                        except Exception:
                            pass  # Best-effort

                elif status in ["running", "processing", "pending", "queued"]:
                    result["completed"] = False
                    # Include timing estimates if available
                    est_remaining = data.get("estimated_remaining_minutes")
                    est_total = data.get("estimated_total_minutes")
                    elapsed = data.get("elapsed_minutes")
                    if est_remaining is not None:
                        result["estimated_remaining_minutes"] = est_remaining
                    if est_total is not None:
                        result["estimated_total_minutes"] = est_total
                    if elapsed is not None:
                        result["elapsed_minutes"] = elapsed

                    # Pass through the service's live heartbeat message and
                    # current step. The engine writes these every ~30s with
                    # "Lambda window N/M: <phase> (Xs elapsed in this phase)"
                    # — the user/UI wants to see this directly. Previously we
                    # dropped these fields and replaced the message with a
                    # which made the polling experience opaque ("stuck at
                    # 20%" was indistinguishable from "production phase
                    # 2:34 in" because the user only saw the canned text).
                    progress_message = data.get("message") or ""
                    progress_step = data.get("step") or ""
                    if progress_message:
                        result["progress_message"] = progress_message
                    if progress_step:
                        result["current_step"] = progress_step

                    # Build informative message. The user-facing live status
                    # comes FIRST so it's visible without parsing past
                    # paragraph one of LLM guidance. Canned guidance follows
                    # so the LLM still knows roughly how long to expect and
                    # not to report as stalled.
                    if est_remaining is not None and est_remaining > 0:
                        poll_interval = "30 seconds" if est_remaining <= 2 else "60 seconds"
                        head = progress_message + " — " if progress_message else ""
                        result["message"] = (
                            f"{head}STILL RUNNING — this is expected, not an error. "
                            f"~{est_remaining} minutes remaining "
                            f"(elapsed: {elapsed or '?'} min / est. total: {est_total or '?'} min). "
                            f"You MUST call get_job_status again in {poll_interval}."
                        )
                    else:
                        extra = ""
                        if service == "novomcp-qm":
                            extra = (
                                " CREST conformer search takes 5-15 minutes. Progress at 10% is NORMAL — "
                                "the computation runs as a single process and jumps to 90%/100% when done. "
                                "Do NOT report this as stuck or stalled. "
                                "Tell the user to check back in a few minutes rather than polling in a loop."
                            )
                        elif service == "novomcp-neb":
                            extra = (
                                " NEB transition state search takes 1-10 minutes depending on molecule size "
                                "and number of images. Progress stays at 10% during computation — this is normal. "
                                "Tell the user to check back in a few minutes."
                            )
                        head = progress_message + " — " if progress_message else ""
                        result["message"] = (
                            f"{head}STILL RUNNING — this is expected, not an error. "
                            f"Job is {status}.{extra} "
                            f"Poll again in 60 seconds, but limit to 2-3 polls per conversation turn."
                        )


                elif status in ["failed", "error"]:
                    result["completed"] = True
                    result["error"] = data.get("message") or data.get("error") or "Job failed"

                    # Update dashboard-aggregator for trackable jobs
                    if service in ("gromacs", "novomcp-qm", "novomcp-neb", "alphaflow"):
                        try:
                            await self.client.patch(
                                f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                                json={"status": "failed", "error_message": result["error"]},
                                headers={"X-Admin-Key": self.dashboard_admin_key},
                                timeout=10.0,
                            )
                        except Exception:
                            pass

                return ToolResult(
                    success=True,
                    data=result,
                    usage={"queries": 0, "tool": "get_job_status"}  # Status checks free
                )

            elif response.status_code == 202:
                return ToolResult(
                    success=True,
                    data={
                        "job_id": job_id,
                        "service": service,
                        "status": "running",
                        "completed": False,
                        "message": "STILL RUNNING — this is expected, not an error. You MUST call get_job_status again in 30 seconds."
                    },
                    usage={"queries": 0, "tool": "get_job_status"}
                )

            elif response.status_code == 404:
                return ToolResult(
                    success=False,
                    error=f"Job not found. It may still be initializing - wait 5-10 seconds and try again."
                )

            return ToolResult(success=False, error=f"Status check error: {response.status_code}")

        except Exception as e:
            # Container may have scaled to zero — fall back to shared Redis
            # where async job services persist status with 7-day TTL.
            redis_fallback_services = {"gromacs", "novomcp-qm", "novomcp-neb", "alphaflow"}
            if service in redis_fallback_services:
                try:
                    r = await self._get_redis()
                    if r:
                        redis_key = f"{self._redis_prefix}:job:{job_id}"
                        job_data = await r.hgetall(redis_key)
                        if job_data:
                            job_data = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v) for k, v in job_data.items()}
                            status = job_data.get("status", "unknown")
                            result = {
                                "job_id": job_id,
                                "service": service,
                                "status": status,
                                "source": "redis_fallback",
                            }
                            progress_raw = job_data.get("progress")
                            if progress_raw:
                                try:
                                    progress = json.loads(progress_raw)
                                    if isinstance(progress, dict):
                                        result["progress_percent"] = progress.get("percentage", 0)
                                        result["current_step"] = progress.get("step", "")
                                        result["progress_message"] = progress.get("message", "")
                                except (json.JSONDecodeError, TypeError):
                                    pass
                            if status == "completed":
                                result["completed"] = True
                                result_raw = job_data.get("result")
                                if result_raw:
                                    try:
                                        result["results"] = json.loads(result_raw)
                                    except (json.JSONDecodeError, TypeError):
                                        pass
                                # Update dashboard-aggregator so list_jobs reflects completion
                                try:
                                    await self.client.patch(
                                        f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                                        json={"status": "completed", "progress_pct": 100},
                                        headers={"X-Admin-Key": self.dashboard_admin_key},
                                        timeout=10.0,
                                    )
                                except Exception:
                                    pass
                            elif status == "failed":
                                result["completed"] = True
                                result["error"] = job_data.get("error", "Job failed")
                            else:
                                result["completed"] = False
                                result["message"] = (
                                    f"Job is {status} (container unreachable, status from Redis). "
                                    f"The compute container may be restarting. Poll again in 60 seconds."
                                )
                            return ToolResult(success=True, data=result, usage={"queries": 0, "tool": "get_job_status"})
                except Exception as redis_err:
                    logger.warning(f"Redis fallback also failed for {job_id}: {redis_err}")

            # k8s-Jobs services (MD/QM/NEB/alphaflow) have no long-running HTTP
            # endpoint post-AWS-migration — status lives in dashboard SQL + Redis. A
            # brand-new job whose SQL row and Redis hash aren't written yet lands here
            # (the _call_service attempt fails because there is no service URL to dial;
            # pre-sweep it DNS-crashed against the decommissioned Azure default host).
            # Report a clean "initializing" so the client keeps polling instead of
            # surfacing a raw connection error.
            redis_backed_jobs = {"gromacs", "novomcp-qm", "novomcp-neb", "alphaflow"}
            if service in redis_backed_jobs:
                logger.info(
                    f"get_job_status: {service} job {job_id} has no live status yet "
                    f"({type(e).__name__}) — reporting initializing"
                )
                return ToolResult(success=True, data={
                    "job_id": job_id,
                    "service": service,
                    "status": "initializing",
                    "completed": False,
                    "source": "pending",
                    "message": (
                        "Job submitted; live status not yet available — the compute Job is "
                        "starting and hasn't written its first heartbeat. Expected for the "
                        "first 1-2 minutes (longer on a cold GPU node). Poll again in 60 seconds."
                    ),
                }, usage={"queries": 0, "tool": "get_job_status"})

            return ToolResult(success=False, error=f"Status check failed: {type(e).__name__}: {str(e)}")

    async def _execute_get_3d_properties(self, args: Dict[str, Any]) -> ToolResult:
        """
        Get 3D molecular properties from NovoMD.

        Returns 32+ properties:
        - Geometry (7): radius_of_gyration, asphericity, eccentricity, inertia_shape_factor, span_r, pmi1, pmi2
        - Energy (6): conformer_energy, vdw_energy, electrostatic_energy, torsion_strain, angle_strain, optimization_delta
        - Electrostatics (6): dipole_moment, total_charge, max/min_partial_charge, charge_span, electrostatic_potential
        - Surface/Volume (4): sasa, molecular_volume, globularity, surface_to_volume_ratio
        - Atom Counts (2): num_atoms_with_h, num_heavy_atoms
        - 3D Visualization (5+): coords_x, coords_y, coords_z, atom_types, bonds
        """
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        force_field = args.get("force_field", "AMBER")
        optimize_3d = args.get("optimize_3d", True)
        include_coordinates = args.get("include_coordinates", True)

        try:
            # Get NovoMD API key from environment
            novomd_api_key = os.getenv("NOVOMD_API_KEY")
            if not novomd_api_key:
                return ToolResult(success=False, error="NovoMD service not configured (missing API key)")

            # Call NovoMD service
            response = await self._call_service(
                "novomd",
                "/smiles-to-omd",
                {
                    "smiles": smiles,
                    "force_field": force_field,
                    "optimize_3d": optimize_3d,
                    "add_hydrogens": True,
                    "box_size": 30.0
                },
                timeout=60.0,  # 3D computation can take time
                api_key=novomd_api_key
            )

            if response.status_code != 200:
                error_detail = ""
                try:
                    error_data = response.json()
                    error_detail = error_data.get("detail", str(response.status_code))
                except Exception:
                    error_detail = str(response.status_code)
                return ToolResult(success=False, error=f"NovoMD error: {error_detail}")

            data = response.json()

            # NovoMD returns properties inside 'metadata' dict
            props = data.get("metadata", {})

            # Extract and organize properties from metadata
            result = {
                "smiles": smiles,
                "force_field": force_field,
                "geometry": {
                    "radius_of_gyration": props.get("radius_of_gyration"),
                    "asphericity": props.get("asphericity"),
                    "eccentricity": props.get("eccentricity"),
                    "inertia_shape_factor": props.get("inertia_shape_factor"),
                    "span_r": props.get("span_r"),
                    "pmi1": props.get("pmi1"),
                    "pmi2": props.get("pmi2"),
                },
                "energy": {
                    "conformer_energy": props.get("conformer_energy"),
                    "vdw_energy": props.get("vdw_energy"),
                    "electrostatic_energy": props.get("electrostatic_energy"),
                    "torsion_strain": props.get("torsion_strain"),
                    "angle_strain": props.get("angle_strain"),
                    "optimization_delta": props.get("optimization_delta"),
                },
                "electrostatics": {
                    "dipole_moment": props.get("dipole_moment"),
                    "total_charge": props.get("total_charge"),
                    "max_partial_charge": props.get("max_partial_charge"),
                    "min_partial_charge": props.get("min_partial_charge"),
                    "charge_span": props.get("charge_span"),
                    "electrostatic_potential": props.get("electrostatic_potential"),
                },
                "surface_volume": {
                    "sasa": props.get("sasa"),
                    "molecular_volume": props.get("molecular_volume"),
                    "globularity": props.get("globularity"),
                    "surface_to_volume_ratio": props.get("surface_to_volume_ratio"),
                },
                "atom_counts": {
                    "num_atoms_with_h": props.get("num_atoms_with_h"),
                    "num_heavy_atoms": props.get("num_heavy_atoms"),
                },
            }

            # Optionally include 3D coordinates (also in metadata)
            if include_coordinates:
                result["coordinates"] = {
                    "coords_x": props.get("coords_x"),
                    "coords_y": props.get("coords_y"),
                    "coords_z": props.get("coords_z"),
                    "atom_types": props.get("atom_types"),
                    "bonds": props.get("bonds"),
                }
                # Include PDB format if available
                if data.get("pdb_content"):
                    result["pdb"] = data.get("pdb_content")

            # Remove None values for cleaner output
            for category in ["geometry", "energy", "electrostatics", "surface_volume", "atom_counts"]:
                result[category] = {k: v for k, v in result[category].items() if v is not None}

            return ToolResult(
                success=True,
                data=result,
                usage={"queries": 1, "tool": "get_3d_properties", "compute_service": "novomd"}
            )

        except httpx.TimeoutException:
            return ToolResult(success=False, error="NovoMD computation timed out (60s limit)")
        except Exception as e:
            logger.exception(f"Error in get_3d_properties: {e}")
            return ToolResult(success=False, error=f"3D property calculation failed: {str(e)}")

    async def _execute_calculate_properties(self, args: Dict[str, Any]) -> ToolResult:
        """
        Calculate RDKit molecular properties via chem-props service.
        Returns Lipinski descriptors, drug-likeness scores, physicochemical properties.
        """
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        include_fingerprints = args.get("include_fingerprints", False)

        chem_props_url = self.service_urls.get("chem-props", "") or os.getenv("CHEM_PROPS_URL", "")

        # No remote service configured → compute in-process via RDKit.
        # This is the default turnkey path for local runs.
        if not chem_props_url:
            props = await self._compute_basic_properties(smiles)
            if "error" in props:
                return ToolResult(success=False, error=f"Property calculation failed: {props['error']}")
            return ToolResult(
                success=True,
                data={"smiles": smiles, "source": "rdkit-local", **props},
                usage={"queries": 1, "tool": "calculate_properties", "compute_service": "rdkit-local"},
            )

        try:
            api_key = os.getenv("CHEM_PROPS_API_KEY")
            response = await self.client.post(
                f"{chem_props_url}/chem-props/calculate_single",
                params={"smiles": smiles},
                headers={"X-API-Key": api_key or self.internal_api_key},
                timeout=30.0
            )

            if response.status_code != 200:
                # Remote returned non-2xx — fall back to in-process RDKit.
                logger.info(f"chem-props returned {response.status_code}, using in-process RDKit fallback")
                props = await self._compute_basic_properties(smiles)
                if "error" not in props:
                    return ToolResult(
                        success=True,
                        data={"smiles": smiles, "source": "rdkit-local", **props},
                        usage={"queries": 1, "tool": "calculate_properties", "compute_service": "rdkit-local"},
                    )
                return ToolResult(success=False, error=f"Property calculation failed: {props.get('error')}")

            data = response.json()
            props = data.get("properties", {})

            # Build structured response from flat chem-props properties
            result_data = {
                "smiles": smiles,
                "source": "chem-props",
                "molecular_weight": props.get("molecular_weight"),
                "exact_mass": props.get("exact_mass"),
                "logp": props.get("logp"),
                "logD": props.get("logD"),
                "tpsa": props.get("tpsa"),
                "volume": props.get("volume"),
                "refractivity": props.get("refractivity"),
                "formal_charge": props.get("formal_charge"),
                "lipinski": {
                    "h_bond_donors": props.get("h_bond_donors"),
                    "h_bond_acceptors": props.get("h_bond_acceptors"),
                    "molecular_weight": props.get("molecular_weight"),
                    "logp": props.get("logp"),
                    "violations": props.get("lipinski_violations"),
                },
                "veber": {
                    "rotatable_bonds": props.get("rotatable_bonds"),
                    "tpsa": props.get("tpsa"),
                    "violations": props.get("veber_violations"),
                    "score": props.get("veber_score"),
                },
                "drug_likeness": {
                    "qed": props.get("qed"),
                    "sa_score": props.get("synthetic_accessibility"),
                    "drug_likeness_score": props.get("drug_likeness"),
                    "solubility_estimate": props.get("solubility_estimate"),
                },
                "structure": {
                    "num_atoms": props.get("num_atoms"),
                    "num_heavy_atoms": props.get("num_heavy_atoms"),
                    "num_rings": props.get("num_rings"),
                    "num_aromatic_rings": props.get("num_aromatic_rings"),
                    "fraction_sp3": props.get("fraction_sp3"),
                    "bertz_complexity": props.get("bertz_ct"),
                    "murcko_scaffold": props.get("murcko_scaffold"),
                },
            }

            return ToolResult(
                success=True,
                data=result_data,
                usage={"queries": 1, "tool": "calculate_properties", "compute_service": "chem-props"}
            )

        except Exception as e:
            logger.info(f"chem-props connection failed ({e}), using in-process RDKit fallback")
            try:
                props = await self._compute_basic_properties(smiles)
                if "error" not in props:
                    return ToolResult(
                        success=True,
                        data={"smiles": smiles, "source": "rdkit-local", **props},
                        usage={"queries": 1, "tool": "calculate_properties", "compute_service": "rdkit-local"},
                    )
                return ToolResult(success=False, error=f"Property calculation failed: {props.get('error')}")
            except Exception as fallback_error:
                logger.exception(f"Fallback also failed: {fallback_error}")
                return ToolResult(success=False, error=f"Property calculation failed: {str(e)}")

    async def _execute_predict_admet(self, args: Dict[str, Any]) -> ToolResult:
        """
        Predict ADMET properties using addie-models service.
        Returns predictions from 31 specialized ML models.
        """
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        models = args.get("models")  # Optional: specific models to run

        try:
            # Get addie-models API key from environment
            api_key = os.getenv("ADDIE_MODELS_API_KEY")

            # Call addie-models service
            # Endpoint is /addie/process (batch processing endpoint)
            import uuid
            mol_id = str(uuid.uuid4())[:8]
            response = await self._call_service(
                "addie-models",
                "/addie/process",
                {
                    "molecules": [{"id": mol_id, "smiles": smiles}],
                    "include_descriptors": True,
                    "include_confidence": True
                },
                timeout=60.0,  # ML inference can take time
                api_key=api_key
            )

            if response.status_code != 200:
                error_detail = str(response.status_code)
                try:
                    error_data = response.json()
                    error_detail = error_data.get("detail", error_detail)
                except Exception:
                    pass
                return ToolResult(success=False, error=f"ADMET prediction failed: {error_detail}")

            data = response.json()

            # Extract predictions for our molecule from batch response
            # Response format: {"results": [{"id": "...", "predictions": {...}}]}
            results = data.get("results", [])
            mol_result = next((r for r in results if r.get("id") == mol_id), {})
            predictions = mol_result.get("predictions", {})

            # Map addie-models predictions into the structured ADMET shape (shared
            # with the batched novel path so both emit identical output).
            result = _build_admet_result(smiles, predictions, mol_result.get("error"))

            return ToolResult(
                success=True,
                data=result,
                usage={"queries": 1, "tool": "predict_admet", "compute_service": "addie-models"}
            )

        except httpx.TimeoutException:
            return ToolResult(success=False, error="ADMET prediction timed out (60s limit)")
        except Exception as e:
            logger.exception(f"Error in predict_admet: {e}")
            return ToolResult(success=False, error=f"ADMET prediction failed: {str(e)}")

    async def _predict_admet_batch_addie(self, smiles_list: List[str]) -> Dict[str, Dict[str, Any]]:
        """WS2: run ADMET for many novel molecules in ONE /addie/process call and
        return {smiles: admet_block}, where admet_block matches the `admet` field
        get_molecule_profile stores for a single novel molecule (smiles/source/
        raw_predictions/properties stripped). Replaces N one-molecule addie calls
        with a single batched call — made fast by WS1's in-addie Chemprop batching.

        Returns {} on failure so callers fall back to leaving novel ADMET absent
        rather than erroring the whole batch."""
        if not smiles_list:
            return {}
        api_key = os.getenv("ADDIE_MODELS_API_KEY")
        import uuid
        id_to_smiles: Dict[str, str] = {}
        molecules = []
        for s in smiles_list:
            mid = str(uuid.uuid4())[:8]
            id_to_smiles[mid] = s
            molecules.append({"id": mid, "smiles": s})

        out: Dict[str, Dict[str, Any]] = {}
        try:
            response = await self._call_service(
                "addie-models",
                "/addie/process",
                {"molecules": molecules, "include_descriptors": True, "include_confidence": True},
                timeout=120.0,  # batched ML inference; WS1 keeps this well under the cap
                api_key=api_key,
            )
            if response.status_code != 200:
                logger.warning(f"batch addie call failed: HTTP {response.status_code}")
                return {}
            data = response.json()
            for r in data.get("results", []):
                smiles = id_to_smiles.get(r.get("id"))
                if smiles is None:
                    continue
                full = _build_admet_result(smiles, r.get("predictions", {}), r.get("error"))
                out[smiles] = {
                    k: v for k, v in full.items()
                    if k not in ("smiles", "source", "raw_predictions", "properties")
                }
        except Exception as e:
            logger.warning(f"batch addie call error: {e}")
            return {}
        return out

    # =========================================================================
    # Clinical Outcomes Prediction (NovoExpert v3)
    # =========================================================================

    # The 63 features expected by novoexpert v3, grouped by source service.
    _NOVOEXPERT_CHEM_PROPS_FEATURES = [
        "molecular_weight", "logp", "tpsa", "hba", "hbd",
        "rotatable_bonds", "aromatic_rings", "qed", "lipinski_violations",
    ]

    _NOVOEXPERT_ADMET_FEATURES = [
        # CYP Inhibition
        "cyp1a2_inhibitor_probability", "cyp2c9_inhibitor_probability",
        "cyp2c19_inhibitor_probability", "cyp2d6_inhibitor_probability",
        "cyp3a4_inhibitor_probability", "cyp_inhibition_risk_score",
        # CYP Substrate
        "cyp2c9_substrate_probability", "cyp2d6_substrate_probability",
        "cyp_substrate_max_probability",
        # Toxicity
        "hepatotoxicity_probability", "cardiotoxicity_max_probability",
        "ames_mutagenicity_probability", "carcinogenicity_probability",
        "clinical_toxicity_probability", "developmental_toxicity_probability",
        "reproductive_toxicity_probability", "respiratory_toxicity_probability",
        "eye_corrosion_probability", "eye_irritation_probability",
        "herg_blocker_probability",
        # Tox21 Nuclear Receptors
        "nr_ahr_agonist_probability", "nr_ar_lbd_agonist_probability",
        "nr_ar_agonist_probability", "nr_aromatase_inhibitor_probability",
        "nr_er_lbd_agonist_probability", "nr_er_agonist_probability",
        "nr_ppar_gamma_agonist_probability",
        # Tox21 Stress Response
        "sr_are_activation_probability", "sr_atad5_activation_probability",
        "sr_hse_activation_probability", "sr_mmp_activation_probability",
        "sr_p53_activation_probability",
        # PK / ADME
        "half_life_hr", "clearance_microsome", "bioavailability_probability",
        "ppbr_percent", "vdss_L_kg", "aqueous_solubility_log_mol_L",
        "caco2_permeability", "hia_probability", "pgp_inhibitor_probability",
        "bbb_penetration_probability", "binding_affinity_score",
    ]

    _NOVOEXPERT_FAVES_FEATURES = [
        "has_structural_alerts", "has_pains", "boiled_egg_class",
        "boiled_egg_in_hia", "boiled_egg_in_bbb", "wlogp",
        "is_aggregator_risk", "synthetic_accessibility",
    ]

    _NOVOEXPERT_CATEGORICAL_FEATURES = [
        "therapeutic_area", "target_type", "action_type",
    ]

    async def _execute_predict_clinical_outcomes(self, args: Dict[str, Any]) -> ToolResult:
        """
        Predict Phase I clinical trial clearance probability.

        Orchestrates three services in parallel to assemble the 63-feature dict,
        then calls novoexpert /predict for calibrated probability + SHAP + competence.
        """
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        therapeutic_area = args.get("therapeutic_area", "UNKNOWN")
        target_type = args.get("target_type", "UNKNOWN")
        action_type = args.get("action_type", "UNKNOWN")
        top_k_shap = min(max(args.get("top_k_shap", 10), 1), 63)

        import asyncio
        import uuid

        try:
            # Step 1: Gather features from three services in parallel
            chem_task = self._novoexpert_get_chem_props(smiles)
            faves_task = self._novoexpert_get_faves(smiles)
            admet_task = self._novoexpert_get_admet(smiles)

            chem_result, faves_result, admet_result = await asyncio.gather(
                chem_task, faves_task, admet_task,
                return_exceptions=True,
            )

            # Step 2: Assemble the 63-feature dict
            features: Dict[str, Any] = {}
            sources_ok = []
            sources_failed = []

            # Chem-props (9 features)
            if isinstance(chem_result, dict) and "error" not in chem_result:
                features.update(chem_result)
                sources_ok.append("chem-props")
            else:
                err = str(chem_result) if isinstance(chem_result, Exception) else chem_result.get("error", "unknown")
                logger.warning(f"predict_clinical_outcomes: chem-props failed: {err}")
                sources_failed.append("chem-props")

            # FAVES (8 features)
            if isinstance(faves_result, dict) and "error" not in faves_result:
                features.update(faves_result)
                sources_ok.append("faves-compliance")
            else:
                err = str(faves_result) if isinstance(faves_result, Exception) else faves_result.get("error", "unknown")
                logger.warning(f"predict_clinical_outcomes: faves-compliance failed: {err}")
                sources_failed.append("faves-compliance")

            # ADMET (43 features)
            if isinstance(admet_result, dict) and "error" not in admet_result:
                features.update(admet_result)
                sources_ok.append("addie-models")
            else:
                err = str(admet_result) if isinstance(admet_result, Exception) else admet_result.get("error", "unknown")
                logger.warning(f"predict_clinical_outcomes: addie-models failed: {err}")
                sources_failed.append("addie-models")

            # Categorical features (from args)
            features["therapeutic_area"] = therapeutic_area
            features["target_type"] = target_type
            features["action_type"] = action_type

            # Step 3: Call novoexpert /predict
            novoexpert_payload = {
                "smiles": smiles,
                "features": features,
                "therapeutic_area": therapeutic_area,
                "target_type": target_type,
                "action_type": action_type,
                "top_k_shap": top_k_shap,
            }

            response = await self._call_service(
                "novoexpert",
                "/predict",
                novoexpert_payload,
                timeout=30.0,
                api_key=self.service_api_keys.get("novoexpert"),
            )

            if response.status_code != 200:
                error_detail = str(response.status_code)
                try:
                    error_data = response.json()
                    error_detail = error_data.get("detail", error_detail)
                except Exception:
                    pass
                return ToolResult(
                    success=False,
                    error=f"NovoExpert prediction failed: {error_detail}",
                )

            data = response.json()

            # Build response
            result_data = {
                "smiles": smiles,
                "phase1_clearance_probability": data.get("phase1_clearance_probability"),
                "phase1_clearance_probability_raw": data.get("phase1_clearance_probability_raw"),
                "calibration": data.get("calibration", "isotonic"),
                "model_version": data.get("model_version"),
                "model_name": data.get("model_name"),
                "competence_check": data.get("competence_check"),
                "top_shap_features": data.get("top_shap_features", []),
                "feature_count": data.get("feature_count", 0),
                "missing_features": data.get("missing_features", []),
                "feature_sources": {
                    "succeeded": sources_ok,
                    "failed": sources_failed,
                },
            }

            return ToolResult(
                success=True,
                data=result_data,
                usage={
                    "queries": 1 + len(sources_ok),
                    "tool": "predict_clinical_outcomes",
                    "compute_services": ["novoexpert"] + sources_ok,
                },
            )

        except httpx.TimeoutException:
            return ToolResult(
                success=False,
                error="Clinical outcomes prediction timed out",
            )
        except Exception as e:
            logger.exception(f"Error in predict_clinical_outcomes: {e}")
            return ToolResult(
                success=False,
                error=f"Clinical outcomes prediction failed: {str(e)}",
            )

    async def _novoexpert_get_chem_props(self, smiles: str) -> Dict[str, Any]:
        """Fetch physicochemical properties from chem-props for novoexpert."""
        try:
            chem_props_url = self.service_urls.get("chem-props", "")
            response = await self.client.post(
                f"{chem_props_url}/chem-props/calculate_single",
                params={"smiles": smiles},
                headers={"X-API-Key": self.service_api_keys.get("chem-props", self.internal_api_key)},
                timeout=30.0,
            )
            if response.status_code != 200:
                # Fallback to faves-compliance /api/classify for basic props
                props = await self._compute_basic_properties(smiles)
                if "error" not in props:
                    return {
                        "molecular_weight": props.get("molecular_weight"),
                        "logp": props.get("logp"),
                        "tpsa": props.get("tpsa"),
                        "hba": props.get("hba"),
                        "hbd": props.get("hbd"),
                        "rotatable_bonds": props.get("rotatable_bonds"),
                        "aromatic_rings": props.get("aromatic_rings"),
                        "qed": props.get("qed"),
                        "lipinski_violations": props.get("lipinski_violations"),
                    }
                return {"error": f"chem-props returned {response.status_code}"}

            data = response.json()
            props = data.get("properties", {})
            return {
                "molecular_weight": props.get("molecular_weight"),
                "logp": props.get("logp"),
                "tpsa": props.get("tpsa"),
                "hba": props.get("h_bond_acceptors"),
                "hbd": props.get("h_bond_donors"),
                "rotatable_bonds": props.get("rotatable_bonds"),
                "aromatic_rings": props.get("num_aromatic_rings"),
                "qed": props.get("qed"),
                "lipinski_violations": props.get("lipinski_violations"),
            }
        except Exception as e:
            return {"error": str(e)}

    async def _novoexpert_get_faves(self, smiles: str) -> Dict[str, Any]:
        """Fetch structural alerts + BOILED-Egg from faves-compliance for novoexpert."""
        try:
            response = await self._call_service(
                "faves-compliance", "/api/classify", {"smiles": smiles}
            )
            if response.status_code != 200:
                return {"error": f"faves-compliance returned {response.status_code}"}

            data = response.json()
            return {
                "has_structural_alerts": data.get("has_structural_alerts", 0),
                "has_pains": data.get("has_pains", 0),
                "boiled_egg_class": data.get("boiled_egg_class", "grey"),
                "boiled_egg_in_hia": data.get("boiled_egg_in_hia", 0),
                "boiled_egg_in_bbb": data.get("boiled_egg_in_bbb", 0),
                "wlogp": data.get("wlogp"),
                "is_aggregator_risk": data.get("is_aggregator_risk", 0),
                "synthetic_accessibility": data.get("synthetic_accessibility"),
            }
        except Exception as e:
            return {"error": str(e)}

    async def _novoexpert_get_admet(self, smiles: str) -> Dict[str, Any]:
        """Fetch ADMET predictions from addie-models for novoexpert."""
        try:
            import uuid
            mol_id = str(uuid.uuid4())[:8]
            response = await self._call_service(
                "addie-models",
                "/addie/process",
                {
                    "molecules": [{"id": mol_id, "smiles": smiles}],
                    "include_descriptors": True,
                    "include_confidence": True,
                },
                timeout=60.0,
                api_key=self.service_api_keys.get("addie-models"),
            )
            if response.status_code != 200:
                return {"error": f"addie-models returned {response.status_code}"}

            data = response.json()
            results = data.get("results", [])
            mol_result = next((r for r in results if r.get("id") == mol_id), {})
            preds = mol_result.get("predictions", {})

            # Extract only the features novoexpert needs (43 ADMET features)
            return {k: preds.get(k) for k in self._NOVOEXPERT_ADMET_FEATURES}
        except Exception as e:
            return {"error": str(e)}

    async def _execute_search_literature(self, args: Dict[str, Any]) -> ToolResult:
        """
        Search curated drug discovery literature from Pinecone.
        Searches across multiple namespaces (uploads, pubmed, preprints) and
        deduplicates results to return unique papers.
        """
        query = args.get("query")
        if not query:
            return ToolResult(success=False, error="Missing required parameter: query")

        top_k = min(args.get("top_k", 10), 20)
        year_min = args.get("year_min")

        try:
            # Import Pinecone client
            from core.pinecone_client import get_pinecone_client
            import asyncio

            pinecone_client = get_pinecone_client()

            # Build filters (only if year filter specified)
            filters = {}
            if year_min:
                filters["year"] = {"$gte": year_min}

            # Request 3x results per namespace to allow for deduplication
            fetch_count = min(top_k * 3, 60)

            # Generate embedding once, reuse across all namespace searches
            query_embedding = await pinecone_client.generate_embeddings(query)

            # Search across all literature namespaces in parallel (with pre-computed embedding)
            namespaces = ["uploads", "pubmed", "preprints"]
            search_tasks = [
                pinecone_client.search_literature(
                    query=query,
                    filters=filters if filters else None,
                    top_k=fetch_count,
                    namespace=ns,
                    query_embedding=query_embedding,
                )
                for ns in namespaces
            ]

            results_per_ns = await asyncio.gather(*search_tasks, return_exceptions=True)

            # Merge results from all namespaces
            raw_papers = []
            for ns, result in zip(namespaces, results_per_ns):
                if isinstance(result, Exception):
                    logger.warning(f"Literature search in namespace '{ns}' failed: {result}")
                    continue
                for paper in result:
                    paper["namespace"] = ns
                    raw_papers.append(paper)

            # Sort by relevance score (descending)
            raw_papers.sort(key=lambda p: p.get("score", 0), reverse=True)

            # Deduplicate per unique paper, keeping the highest-scoring chunk.
            # Canonical identifiers first (pmcid/doi/pmid are stable across the
            # chunks of one paper); fall back to a normalized title, then the
            # chunk id. Title is normalized (strip + lowercase) so whitespace
            # drift between chunks doesn't defeat the dedup. The PMC `uploads`
            # corpus has no doi, so the prior `doi or title` key leaned entirely
            # on exact-title matching — pmcid makes it robust.
            seen_papers = {}
            for paper in raw_papers:
                paper_key = (
                    paper.get("pmcid")
                    or paper.get("doi")
                    or paper.get("pmid")
                    or (paper.get("title") or "").strip().lower()
                    or paper.get("id")
                )
                if not paper_key:
                    continue

                # Keep only the first (highest-scoring) result per paper
                if paper_key not in seen_papers:
                    seen_papers[paper_key] = paper

            # Convert back to list and limit to requested top_k
            papers = list(seen_papers.values())[:top_k]

            return ToolResult(
                success=True,
                data={
                    "query": query,
                    "total_results": len(papers),
                    "papers": papers,
                    "tool_suggestions": [
                        self._tool_suggestion(
                            "search_biorxiv",
                            "Search bioRxiv/medRxiv for recent preprints (may include unpublished cutting-edge research)"
                        ),
                        self._tool_suggestion(
                            "search_clinical_trials",
                            "Find related clinical trials to see how research translates to clinical practice"
                        )
                    ]
                },
                usage={"queries": 1, "tool": "search_literature"}
            )

        except ValueError as e:
            # Pinecone not configured
            return ToolResult(success=False, error=f"Literature search not configured: {str(e)}")
        except Exception as e:
            logger.exception(f"Error in search_literature: {e}")
            return ToolResult(success=False, error=f"Literature search failed: {str(e)}")

    async def _execute_search_patents(self, args: Dict[str, Any]) -> ToolResult:
        """
        Search USPTO pharmaceutical patents from Pinecone.
        """
        query = args.get("query")
        if not query:
            return ToolResult(success=False, error="Missing required parameter: query")

        top_k = min(args.get("top_k", 10), 20)
        year_min = args.get("year_min")

        try:
            # Import Pinecone client
            from core.pinecone_client import get_pinecone_client

            pinecone_client = get_pinecone_client()

            # Build filters if year filter provided
            filters = {}
            if year_min:
                filters["year"] = {"$gte": year_min}

            # Search patents namespace (separate from literature uploads namespace)
            results = await pinecone_client.search_literature(
                query=query,
                filters=filters if filters else None,
                top_k=top_k,
                namespace="patents"  # 1,187 USPTO patents
            )

            # Format patent results
            patents = []
            for result in results:
                # Extract patent number from ID (format: "patent_USPTO_XXXXXXXX_chunk_X")
                result_id = result.get("id", "")
                patent_number = None
                if "USPTO_" in result_id:
                    # Extract the number after USPTO_
                    parts = result_id.split("USPTO_")
                    if len(parts) > 1:
                        num_part = parts[1].split("_")[0]  # Get number before _chunk
                        patent_number = f"US{num_part}"

                patents.append({
                    "id": result_id,
                    "title": result.get("title"),
                    "abstract": result.get("abstract"),
                    # Patents store applicant in "authors" field in Pinecone metadata
                    "applicant": result.get("authors") or [],
                    # Patents store filing date as "year" in Pinecone metadata
                    "filing_date": result.get("year") or "",
                    "patent_number": patent_number,
                    "relevance": result.get("relevance") or result.get("score"),
                })

            return ToolResult(
                success=True,
                data={
                    "query": query,
                    "total_results": len(patents),
                    "patents": patents
                },
                usage={"queries": 1, "tool": "search_patents"}
            )

        except ValueError as e:
            # Pinecone not configured
            return ToolResult(success=False, error=f"Patent search not configured: {str(e)}")
        except Exception as e:
            logger.exception(f"Error in search_patents: {e}")
            return ToolResult(success=False, error=f"Patent search failed: {str(e)}")

    # =========================================================================
    # Research Database Tools (External APIs)
    # =========================================================================

    async def _execute_search_biorxiv(self, args: Dict[str, Any]) -> ToolResult:
        """
        Search bioRxiv/medRxiv preprint servers.
        Uses the bioRxiv API: https://api.biorxiv.org/

        bioRxiv's /details endpoint returns papers in 100-item pages by date,
        not by query. We fetch one page, filter in-memory, and fall back to a
        shorter date window on timeout.
        """
        query = args.get("query")
        server = args.get("server", "biorxiv")
        top_k = min(args.get("top_k", 10), 30)
        days_back = args.get("days_back", 180)  # Reduced default from 365 to 180

        if not query:
            return ToolResult(success=False, error="Missing required parameter: query")

        try:
            from datetime import datetime, timedelta

            async def _fetch_biorxiv(window_days: int, timeout_s: float) -> list:
                """Fetch one 100-paper page from bioRxiv within the given window."""
                end_date = datetime.now().strftime("%Y-%m-%d")
                start_date = (datetime.now() - timedelta(days=window_days)).strftime("%Y-%m-%d")
                api_url = f"https://api.biorxiv.org/details/{server}/{start_date}/{end_date}/0/100"
                async with httpx.AsyncClient(timeout=timeout_s) as client:
                    response = await client.get(api_url)
                    response.raise_for_status()
                    return response.json().get("collection", [])

            # Try the requested window first (60s timeout — bioRxiv can be slow)
            collection = []
            start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            end_date = datetime.now().strftime("%Y-%m-%d")
            try:
                collection = await _fetch_biorxiv(days_back, 60.0)
            except (httpx.TimeoutException, httpx.ReadTimeout):
                # Fallback: try a 60-day window with shorter timeout
                logger.warning(f"bioRxiv timeout on {days_back}-day window, retrying with 60-day window")
                try:
                    collection = await _fetch_biorxiv(60, 30.0)
                    start_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
                except (httpx.TimeoutException, httpx.ReadTimeout):
                    logger.warning("bioRxiv timeout on 60-day fallback — returning empty results")
                    collection = []

            # Filter results by query (case-insensitive search in title and abstract)
            # Support multi-term queries: match if ANY whitespace-split term appears
            query_terms = [t.strip().lower() for t in query.split() if len(t.strip()) >= 3]
            if not query_terms:
                query_terms = [query.lower()]
            matching_papers = []

            for paper in collection:
                title = paper.get("title", "").lower()
                abstract = paper.get("abstract", "").lower()

                # Match if any term appears in title or abstract
                if any(term in title or term in abstract for term in query_terms):
                    matching_papers.append({
                        "doi": paper.get("doi"),
                        "title": paper.get("title"),
                        "abstract": paper.get("abstract", "")[:500] + "..." if len(paper.get("abstract", "")) > 500 else paper.get("abstract", ""),
                        "authors": paper.get("authors"),
                        "date": paper.get("date"),
                        "category": paper.get("category"),
                        "server": server,
                        "url": f"https://www.{server}.org/content/{paper.get('doi')}"
                    })

                    if len(matching_papers) >= top_k:
                        break

            return ToolResult(
                success=True,
                data={
                    "query": query,
                    "server": server,
                    "date_range": f"{start_date} to {end_date}",
                    "total_results": len(matching_papers),
                    "preprints": matching_papers
                },
                usage={"queries": 1, "tool": "search_biorxiv"}
            )

        except httpx.HTTPStatusError as e:
            logger.warning(f"bioRxiv HTTP {e.response.status_code}: {str(e)[:200]}")
            return ToolResult(
                success=False,
                error=f"bioRxiv returned HTTP {e.response.status_code}. "
                      f"{'Rate limited — retry in 60s' if e.response.status_code == 429 else 'Upstream error, try again.'}"
            )
        except Exception as e:
            logger.exception(f"Error in search_biorxiv: {e}")
            return ToolResult(success=False, error=f"bioRxiv search failed: {str(e)[:300]}")

    async def _execute_search_chembl(self, args: Dict[str, Any]) -> ToolResult:
        """
        Search ChEMBL database for compounds, targets, or activities.
        Uses the ChEMBL API: https://www.ebi.ac.uk/chembl/api/data/
        """
        query = args.get("query")
        search_type = args.get("search_type", "compound")
        top_k = min(args.get("top_k", 10), 25)

        if not query:
            return ToolResult(success=False, error="Missing required parameter: query")

        # Validate ChEMBL ID if the query looks like one
        from core.validators import validate_chembl_query
        chembl_val = await validate_chembl_query(query)
        if not chembl_val.valid:
            return ToolResult(
                success=False,
                error=chembl_val.message or f"ChEMBL query '{query}' not valid.",
            )

        try:
            base_url = "https://www.ebi.ac.uk/chembl/api/data"
            results = []

            async with httpx.AsyncClient(timeout=30.0) as client:
                if search_type == "compound":
                    # Search for molecules by name
                    url = f"{base_url}/molecule/search.json?q={query}&limit={top_k}"
                    response = await client.get(url)
                    response.raise_for_status()
                    data = response.json()

                    for mol in data.get("molecules", []):
                        results.append({
                            "chembl_id": mol.get("molecule_chembl_id"),
                            "name": mol.get("pref_name"),
                            "molecule_type": mol.get("molecule_type"),
                            "max_phase": mol.get("max_phase"),
                            "molecular_formula": mol.get("molecule_properties", {}).get("full_molformula") if mol.get("molecule_properties") else None,
                            "molecular_weight": mol.get("molecule_properties", {}).get("full_mwt") if mol.get("molecule_properties") else None,
                            "smiles": mol.get("molecule_structures", {}).get("canonical_smiles") if mol.get("molecule_structures") else None,
                            "first_approval": mol.get("first_approval"),
                            "oral": mol.get("oral"),
                            "indication_class": mol.get("indication_class")
                        })

                elif search_type == "target":
                    # Search for targets
                    url = f"{base_url}/target/search.json?q={query}&limit={top_k}"
                    response = await client.get(url)
                    response.raise_for_status()
                    data = response.json()

                    for target in data.get("targets", []):
                        results.append({
                            "chembl_id": target.get("target_chembl_id"),
                            "name": target.get("pref_name"),
                            "target_type": target.get("target_type"),
                            "organism": target.get("organism"),
                            "target_components": [
                                {"accession": c.get("accession"), "description": c.get("component_description")}
                                for c in target.get("target_components", [])[:3]
                            ]
                        })

                elif search_type == "activity":
                    # Search for bioactivity data
                    # First find the target/compound, then get activities
                    url = f"{base_url}/activity/search.json?q={query}&limit={top_k}"
                    response = await client.get(url)
                    response.raise_for_status()
                    data = response.json()

                    for activity in data.get("activities", []):
                        results.append({
                            "activity_id": activity.get("activity_id"),
                            "molecule_chembl_id": activity.get("molecule_chembl_id"),
                            "target_chembl_id": activity.get("target_chembl_id"),
                            "target_name": activity.get("target_pref_name"),
                            "assay_type": activity.get("assay_type"),
                            "standard_type": activity.get("standard_type"),
                            "standard_value": activity.get("standard_value"),
                            "standard_units": activity.get("standard_units"),
                            "pchembl_value": activity.get("pchembl_value")
                        })

            return ToolResult(
                success=True,
                data={
                    "query": query,
                    "search_type": search_type,
                    "total_results": len(results),
                    "results": results,
                    "tool_suggestions": [
                        self._tool_suggestion(
                            "get_molecule_profile",
                            "Get full profile (ADMET, compliance) for any ChEMBL compound SMILES"
                        ),
                        self._tool_suggestion(
                            "search_similar",
                            "Find structurally similar molecules in the enriched compound index"
                        )
                    ]
                },
                usage={"queries": 1, "tool": "search_chembl"}
            )

        except Exception as e:
            logger.exception(f"Error in search_chembl: {e}")
            return ToolResult(success=False, error=f"ChEMBL search failed: {str(e)}")

    async def _execute_search_clinical_trials(self, args: Dict[str, Any]) -> ToolResult:
        """
        Search ClinicalTrials.gov for clinical studies.
        Uses the ClinicalTrials.gov API v2: https://clinicaltrials.gov/api/v2/
        """
        query = args.get("query")
        condition = args.get("condition")
        status = args.get("status") or "ALL"
        phase = args.get("phase") or "ALL"
        top_k = min(args.get("top_k", 10), 25)

        if not query and not condition:
            return ToolResult(success=False, error="Missing required parameter: query or condition")

        try:
            # Build query parameters — use query.cond for disease terms,
            # query.term for everything else to avoid 400s on complex queries
            params = {
                "pageSize": top_k,
                "format": "json",
                "countTotal": "true"
            }

            if condition:
                # Explicit condition field — use dedicated API parameter
                params["query.cond"] = condition
                if query:
                    params["query.term"] = query[:200]
            elif query:
                params["query.term"] = query[:200]

            # Add status filter
            if status != "ALL":
                status_map = {
                    "RECRUITING": "RECRUITING",
                    "ACTIVE_NOT_RECRUITING": "ACTIVE_NOT_RECRUITING",
                    "COMPLETED": "COMPLETED",
                    "TERMINATED": "TERMINATED"
                }
                if status in status_map:
                    params["filter.overallStatus"] = status_map[status]

            # Add phase filter
            if phase != "ALL":
                phase_map = {
                    "PHASE1": "PHASE1",
                    "PHASE2": "PHASE2",
                    "PHASE3": "PHASE3",
                    "PHASE4": "PHASE4"
                }
                if phase in phase_map:
                    params["filter.phase"] = phase_map[phase]

            url = "https://clinicaltrials.gov/api/v2/studies"

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=params)

                # ClinicalTrials.gov v2 API rejects complex queries — simplify and retry
                if response.status_code == 400:
                    query_val = params.get("query.term") or params.get("query.cond", "")
                    # Keep only first 3 words, strip special characters
                    simplified = " ".join(query_val.split()[:3])
                    logger.warning(f"ClinicalTrials.gov 400 on '{query_val}', retrying with '{simplified}'")
                    # Remove filters that may also cause issues
                    retry_params = {"pageSize": top_k, "format": "json", "query.term": simplified}
                    response = await client.get(url, params=retry_params)

                response.raise_for_status()
                data = response.json()

            trials = []
            for study in data.get("studies", []):
                protocol = study.get("protocolSection", {})
                id_module = protocol.get("identificationModule", {})
                status_module = protocol.get("statusModule", {})
                design_module = protocol.get("designModule", {})
                desc_module = protocol.get("descriptionModule", {})
                conditions_module = protocol.get("conditionsModule", {})
                interventions_module = protocol.get("armsInterventionsModule", {})
                sponsor_module = protocol.get("sponsorCollaboratorsModule", {})
                eligibility_module = protocol.get("eligibilityModule", {})

                trials.append({
                    "nct_id": id_module.get("nctId"),
                    "title": id_module.get("briefTitle"),
                    "status": status_module.get("overallStatus"),
                    "phase": ", ".join(design_module.get("phases", [])),
                    "study_type": design_module.get("studyType"),
                    "conditions": conditions_module.get("conditions", [])[:5],
                    "interventions": [
                        {"type": i.get("type"), "name": i.get("name")}
                        for i in interventions_module.get("interventions", [])[:3]
                    ],
                    "sponsor": sponsor_module.get("leadSponsor", {}).get("name"),
                    "enrollment": eligibility_module.get("maximumAge"),
                    "start_date": status_module.get("startDateStruct", {}).get("date"),
                    "completion_date": status_module.get("completionDateStruct", {}).get("date"),
                    "brief_summary": desc_module.get("briefSummary", "")[:300] + "..." if len(desc_module.get("briefSummary", "")) > 300 else desc_module.get("briefSummary", ""),
                    "url": f"https://clinicaltrials.gov/study/{id_module.get('nctId')}"
                })

            result_data = {
                    "query": query or condition,
                    "status_filter": status,
                    "phase_filter": phase,
                    "total_results": len(trials),
                    "total_count": data.get("totalCount", len(trials)),
                    "trials": trials,
            }

            # Soft diagnostic when no results found — never reject, just explain
            if not trials:
                search_term = condition or query
                result_data["message"] = (
                    f"No clinical trials found for '{search_term}'"
                    f"{' with status=' + status if status != 'ALL' else ''}"
                    f"{' and phase=' + phase if phase != 'ALL' else ''}. "
                    f"Try a broader search term, remove status/phase filters, "
                    f"or check spelling. ClinicalTrials.gov uses MeSH terms — "
                    f"e.g., 'neoplasms' instead of 'cancer', 'glioblastoma' "
                    f"instead of 'brain cancer'."
                )

            return ToolResult(
                success=True,
                data=result_data,
                usage={"queries": 1, "tool": "search_clinical_trials"}
            )

        except httpx.TimeoutException as e:
            logger.warning(f"ClinicalTrials.gov timeout: {e}")
            return ToolResult(
                success=False,
                error=f"ClinicalTrials.gov API timeout. Try a simpler query (fewer terms) or retry. Upstream may be slow."
            )
        except httpx.HTTPStatusError as e:
            logger.warning(f"ClinicalTrials.gov HTTP {e.response.status_code}: {str(e)[:200]}")
            return ToolResult(
                success=False,
                error=f"ClinicalTrials.gov returned HTTP {e.response.status_code}. "
                      f"{'Rate limited — retry in 60s' if e.response.status_code == 429 else 'Upstream error, try again or simplify query.'}"
            )
        except Exception as e:
            logger.exception(f"Error in search_clinical_trials: {e}")
            return ToolResult(success=False, error=f"Clinical trials search failed: {str(e)[:300]}")

    # =========================================================================
    # Enterprise Tier Tools - Context-Dependent Compliance
    # =========================================================================

    async def _execute_check_compliance(self, args: Dict[str, Any]) -> ToolResult:
        """Context-dependent compliance assessment.

        The MCP input schema accepts `context` as a structured object with
        `intended_use`, `jurisdiction`, and optional `therapeutic_area`. The
        upstream faves-compliance `/api/context-compliance` endpoint, however,
        expects a flat request where `context` is a plain research-context
        string ("oncology", "neurology", etc.) and `intended_use`,
        `target_population`, and `regulatory_region` are flat sibling fields.

        We flatten the client's dict into the endpoint's expected shape here.
        Previously the dict was passed through unchanged, causing Pydantic to
        reject the request with HTTP 422 on every call.
        """
        smiles = args.get("smiles")
        context = args.get("context", {}) or {}

        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")
        if not isinstance(context, dict):
            return ToolResult(
                success=False,
                error="'context' must be an object with 'intended_use' and 'jurisdiction'",
            )
        if not context.get("intended_use") or not context.get("jurisdiction"):
            return ToolResult(
                success=False,
                error="Missing required context: intended_use and jurisdiction are required"
            )

        try:
            # First get base profile (pre-computed or FAVES context-free).
            # The result is returned to the caller as `base_compliance`, but
            # we do NOT forward it to the faves-compliance endpoint — that
            # endpoint recomputes its own base classification internally.
            profile_result = await self._execute_get_molecule_profile({"smiles": smiles})
            base_compliance = profile_result.data.get("compliance", {}) if profile_result.success else {}

            # Flatten the structured context into the endpoint's flat schema.
            # The research-context string is derived from therapeutic_area if
            # provided; otherwise fall back to the intended_use (e.g.,
            # "pharmaceutical") which reads reasonably in the endpoint's
            # downstream prose.
            research_context = (
                context.get("therapeutic_area")
                or context.get("research_context")
                or context.get("intended_use")
                or "pharmaceutical development"
            )

            response = await self._call_service(
                "faves-compliance",
                "/api/context-compliance",
                {
                    "smiles": smiles,
                    "context": research_context,
                    "intended_use": context.get("intended_use"),
                    "target_population": context.get("target_population"),
                    "regulatory_region": context.get("jurisdiction", "US"),
                },
                # 90s to absorb faves-compliance cold-start. Warm calls return
                # in <1s; without this budget, cold calls raise httpx.ReadTimeout
                # which surfaces as an empty "Compliance check failed: " error.
                timeout=90.0
            )

            if response.status_code == 200:
                context_result = response.json()

                # Normalize FAVES internal status vocabulary to user-facing labels
                # FAVES returns: PASS, BLOCKED, CONDITIONAL, REVIEW_REQUIRED, DEGRADED
                # MCP tool returns: PROCEED, STOP, CAUTION (matches FUNNEL_SUPPLEMENT)
                raw_status = (context_result.get("overall_status") or "").upper()
                status_map = {
                    "PASS": "PROCEED",
                    "BLOCKED": "STOP",
                    "CONDITIONAL": "CAUTION",
                    "REVIEW_REQUIRED": "CAUTION",
                    "DEGRADED": "CAUTION",
                }
                normalized_status = status_map.get(raw_status, raw_status or "unknown")

                # FDA-whitelisted compounds override to PROCEED regardless of
                # structural alerts. The alerts are scientifically correct but
                # the headline verdict should reflect that a currently marketed
                # FDA-approved drug has already been evaluated for safety.
                # Alerts still surface in context_compliance details.
                #
                # The 122M Aurora cache does not populate `is_whitelisted` for
                # the V3 set (see TODO below), so the cached base_compliance
                # alone misses aspirin/ibuprofen/cortisol/etc. Fall back to the
                # freshly-computed V3 verdict returned by faves-compliance on
                # this very call — it's authoritative and per-request.
                # TODO: populate compliance.is_whitelisted in
                # the molecules table for the ~40 V3-named compounds so all
                # consumers of get_molecule_profile see the flag, not just
                # check_compliance. Tracked in MEMORY.md.
                v3_classification = (context_result.get("base_classification") or {}).get("faves_v3") or {}
                is_whitelisted = (
                    base_compliance.get("is_whitelisted")
                    or base_compliance.get("status") == "whitelisted"
                    or v3_classification.get("is_whitelisted") is True
                )
                if is_whitelisted and normalized_status in ("CAUTION", "unknown"):
                    normalized_status = "PROCEED"

                return ToolResult(
                    success=True,
                    data={
                        "smiles": smiles,
                        "context": context,
                        "base_compliance": base_compliance,
                        "context_compliance": context_result,
                        "overall_status": normalized_status,
                        "raw_overall_status": raw_status if raw_status else None,
                        "recommendations": context_result.get("recommendations", []),
                        "regulatory_pathway": context_result.get("regulatory_pathway"),
                        "risk_assessment": context_result.get("risk_assessment")
                    },
                    usage={"queries": 2, "tool": "check_compliance"}
                )
            # Non-200: surface upstream diagnostic so schema drift is
            # immediately debuggable instead of hiding behind a bare HTTP code.
            try:
                body = response.json()
                detail = body.get("detail") or body.get("error") or str(body)
            except Exception:
                detail = (response.text or "")[:300]
            logger.warning(
                f"check_compliance upstream {response.status_code}: {detail}"
            )
            return ToolResult(
                success=False,
                error=(
                    f"Compliance service returned HTTP {response.status_code}. "
                    f"Upstream detail: {detail}"
                ),
            )

        except httpx.TimeoutException as e:
            logger.warning(f"check_compliance timed out for {smiles}: {type(e).__name__}")
            return ToolResult(
                success=False,
                error=(
                    "Compliance service did not respond in time "
                    "(faves-compliance may be cold-starting; retry in 30s)."
                ),
            )
        except Exception as e:
            # Include the exception class so a blank str(e) doesn't produce
            # a silent "Compliance check failed: " with no reason attached.
            reason = str(e)[:300] or type(e).__name__
            logger.exception(f"check_compliance failed for {smiles}: {type(e).__name__}: {e}")
            return ToolResult(
                success=False,
                error=f"Compliance check failed: {reason}",
            )

    async def _execute_screen_library(self, args: Dict[str, Any]) -> ToolResult:
        """Screen compound library with optional context-dependent assessment."""
        smiles_list = args.get("smiles_list", [])
        if not smiles_list:
            return ToolResult(success=False, error="Missing required parameter: smiles_list")
        if len(smiles_list) > 1000:
            return ToolResult(success=False, error="Library size exceeds maximum (1000)")

        context = args.get("context")
        output_format = args.get("output_format", "summary")

        results = []
        stats = {
            "total": len(smiles_list),
            "known": 0, "novel": 0,
            "clean": 0, "flagged": 0, "controlled": 0
        }

        CHUNK = 50

        # Phase 1 (WS2): profile every molecule concurrently WITHOUT the per-molecule
        # novel addie call (was a strict serial loop — one addie round-trip per novel
        # molecule). Known molecules carry pre-computed corpus ADMET from here.
        async def profile_one(smiles: str) -> Dict[str, Any]:
            try:
                p = await self._execute_get_molecule_profile({"smiles": smiles, "include_admet": False})
                if not p.success or p.data is None:
                    return {"smiles": smiles, "error": p.error or "profile failed"}
                return p.data
            except Exception as e:
                return {"smiles": smiles, "error": str(e)[:200]}

        for i in range(0, len(smiles_list), CHUNK):
            chunk = smiles_list[i : i + CHUNK]
            results.extend(await asyncio.gather(*(profile_one(s) for s in chunk)))

        # Phase 2 (WS2 + Phase-1 bridge): batched ADMET. Novel molecules get full ADMET;
        # known molecules also go through addie to overlay the corpus-stale retrained
        # heads (nr/sr/clinical/cardiotox_dict) live. Chunks fan out across pods.
        batch_smiles = [
            r["smiles"] for r in results if not r.get("error") and r.get("smiles")
        ]
        if batch_smiles:
            chunks = [batch_smiles[i : i + CHUNK] for i in range(0, len(batch_smiles), CHUNK)]
            maps = await asyncio.gather(*(self._predict_admet_batch_addie(c) for c in chunks))
            admet_map: Dict[str, Dict[str, Any]] = {}
            for m in maps:
                admet_map.update(m)
            for r in results:
                block = admet_map.get(r.get("smiles"))
                if block is None:
                    continue
                if r.get("in_database") is True:
                    corpus = r.get("admet") if isinstance(r.get("admet"), dict) else {}
                    r["admet"] = _overlay_bridge_live(corpus, block)
                else:
                    r["admet"] = block
                    r["admet_available"] = True
                    r["source"] = "computed+admet"
                    r.pop("note", None)

        # Phase 3: stats + optional context-dependent compliance (concurrent).
        for entry in results:
            if entry.get("error"):
                continue
            status = entry.get("compliance", {}).get("status", "unknown")
            if entry.get("in_database"):
                stats["known"] += 1
            else:
                stats["novel"] += 1
            if status == "clean":
                stats["clean"] += 1
            elif status == "controlled":
                stats["controlled"] += 1
            elif status == "flagged":
                stats["flagged"] += 1

        if context and context.get("intended_use") and context.get("jurisdiction"):
            async def ctx_one(entry: Dict[str, Any]) -> None:
                if entry.get("error") or not entry.get("smiles"):
                    return
                cr = await self._execute_check_compliance({"smiles": entry["smiles"], "context": context})
                if cr.success:
                    entry["context_compliance"] = cr.data.get("context_compliance")
            for i in range(0, len(results), CHUNK):
                await asyncio.gather(*(ctx_one(e) for e in results[i : i + CHUNK]))

        # Format output
        if output_format == "flagged_only":
            results = [r for r in results if r.get("compliance", {}).get("status") in ["flagged", "controlled"]]
        elif output_format == "summary":
            # Include full results but add summary at top
            pass

        return ToolResult(
            success=True,
            data={
                "summary": stats,
                "context_applied": context is not None,
                "results": results
            },
            usage={"queries": len(smiles_list), "tool": "screen_library"}
        )

    # =========================================================================
    # Enterprise Data Export — Unified Handler (v3.1)
    # Dispatches to individual handlers based on "action" parameter
    # =========================================================================

    async def _execute_push_to_destination(self, arguments: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Unified data export tool — dispatches to list/discover/preview/export handlers."""
        user_tier = (context or {}).get("user_tier", "free")
        if user_tier not in ("team", "enterprise"):
            return ToolResult(
                success=False,
                error="Data connectors require the Scale plan ($500/mo). Upgrade at https://novomcp.com/pricing",
            )

        action = arguments.get("action")
        if not action:
            return ToolResult(success=False, error="'action' is required. Use: list_connections, discover_schema, preview_mapping, or export")

        if action == "list_connections":
            return await self._execute_list_connections(arguments, context)
        elif action == "discover_schema":
            return await self._execute_discover_schema(arguments, context)
        elif action == "preview_mapping":
            return await self._execute_preview_mapping(arguments, context)
        elif action == "export":
            return await self._execute_export_results(arguments, context)
        else:
            return ToolResult(
                success=False,
                error=f"Unknown action: '{action}'. Valid actions: list_connections, discover_schema, preview_mapping, export"
            )

    # --- Individual export handlers (called by _execute_push_to_destination) ---

    async def _execute_list_connections(self, arguments: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """List configured export connections for the user's organization."""
        context = context or {}
        org_id = context.get("org_id")
        if not org_id:
            return ToolResult(success=False, error="Organization context required")

        connector_type = arguments.get("connector_type")
        include_mappings = arguments.get("include_mappings", False)

        try:
            params = {"org_id": org_id}
            if connector_type:
                params["connector_type"] = connector_type

            response = await self.client.get(
                f"{self.dashboard_url}/mcp/connections",
                params=params,
                headers={"X-API-Key": self.dashboard_api_key},
            )

            if response.status_code != 200:
                return ToolResult(success=False, error=f"Failed to fetch connections: HTTP {response.status_code}")

            data = response.json()
            connections = data.get("connections", [])

            # Optionally fetch mappings for each connection
            if include_mappings and connections:
                for conn in connections:
                    try:
                        map_resp = await self.client.get(
                            f"{self.dashboard_url}/mcp/connections/{conn['connection_id']}/mappings",
                            params={"org_id": org_id},
                            headers={"X-API-Key": self.dashboard_api_key},
                        )
                        if map_resp.status_code == 200:
                            conn["mappings"] = map_resp.json().get("mappings", [])
                    except Exception:
                        conn["mappings"] = []

            if not connections:
                return ToolResult(
                    success=True,
                    data={
                        "connections": [],
                        "count": 0,
                        "message": "No connections configured. Ask your admin to set up a connection (Snowflake, BigQuery, Google Sheets, Databricks, Salesforce, PostgreSQL, Notion, Benchling, or Supabase).",
                    },
                    usage={"tool": "list_connections"},
                )

            return ToolResult(
                success=True,
                data={
                    "connections": connections,
                    "count": len(connections),
                    "available_types": ["snowflake", "google_sheets", "bigquery", "databricks", "salesforce", "postgresql", "notion", "benchling", "supabase"],
                },
                usage={"tool": "list_connections"},
            )

        except Exception as e:
            logger.error(f"list_connections failed: {e}")
            return ToolResult(success=False, error=str(e))

    async def _execute_discover_schema(self, arguments: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Discover target schema for a connection (via novomcp-bridge)."""
        context = context or {}
        org_id = context.get("org_id")
        if not org_id:
            return ToolResult(success=False, error="Organization context required")

        connection_id = arguments.get("connection_id")
        if not connection_id:
            return ToolResult(success=False, error="connection_id is required")

        try:
            resp = await self.client.post(
                f"{self.bridge_url}/discover-schema",
                json={
                    "connection_id": connection_id,
                    "org_id": org_id,
                    "user_tier": context.get("user_tier", "free"),
                    "target_filter": arguments.get("target_filter"),
                },
                headers={"X-Bridge-Key": self.bridge_key},
                timeout=120.0,
            )

            if resp.status_code != 200:
                return ToolResult(success=False, error=f"Bridge discover-schema failed: HTTP {resp.status_code}")

            data = resp.json()
            return ToolResult(
                success=True,
                data=data,
                usage={"tool": "discover_schema"},
            )

        except Exception as e:
            logger.error(f"discover_schema failed: {type(e).__name__}: {e}")
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)

    async def _execute_preview_mapping(self, arguments: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Preview field mapping before export (via novomcp-bridge)."""
        context = context or {}
        org_id = context.get("org_id")
        if not org_id:
            return ToolResult(success=False, error="Organization context required")

        connection_id = arguments.get("connection_id")
        data = arguments.get("data")
        source_tool = arguments.get("source_tool")

        if not all([connection_id, data, source_tool]):
            return ToolResult(success=False, error="connection_id, data, and source_tool are required")

        try:
            resp = await self.client.post(
                f"{self.bridge_url}/preview-mapping",
                json={
                    "connection_id": connection_id,
                    "org_id": org_id,
                    "data": data,
                    "source_tool": source_tool,
                    "mapping_id": arguments.get("mapping_id"),
                },
                headers={"X-Bridge-Key": self.bridge_key},
            )

            if resp.status_code != 200:
                return ToolResult(success=False, error=f"Bridge preview-mapping failed: HTTP {resp.status_code}")

            result = resp.json()
            result.update(self._tool_suggestion("export_results", "Use export_results to send this data to the destination"))

            return ToolResult(
                success=True,
                data=result,
                usage={"tool": "preview_mapping"},
            )

        except Exception as e:
            logger.error(f"preview_mapping failed: {e}")
            return ToolResult(success=False, error=str(e))

    # -------------------------------------------------------------------------
    # Export data enrichment — auto-fill sparse data before sending to bridge
    # -------------------------------------------------------------------------

    def _flatten_prediction_data(self, nested: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively flatten nested prediction data to a flat dict of leaf values."""
        flat: Dict[str, Any] = {}
        skip_keys = {"source", "raw_predictions", "in_database", "tool_suggestion",
                      "note", "admet_available", "structural_alerts"}
        for key, value in nested.items():
            if key in skip_keys:
                continue
            if isinstance(value, dict):
                flat.update(self._flatten_prediction_data(value))
            elif value is not None:
                flat[key] = value
        return flat

    @staticmethod
    def _extract_audit_summary(tool_name: str, data: Dict[str, Any]) -> tuple:
        """Extract key results and flags from a tool result for the audit log.

        Returns (key_results: dict, flags: list[str]).
        """
        key_results = {}
        flags = []

        if tool_name == "predict_admet":
            tox = data.get("toxicity", {})
            if isinstance(tox, dict):
                for key in ("herg", "dili", "ames_toxicity"):
                    val = tox.get(key, {})
                    if isinstance(val, dict):
                        prob = val.get("value") or val.get("probability")
                        if prob is not None:
                            key_results[key] = round(float(prob), 3)
                            if float(prob) > 0.5:
                                flags.append(f"{key}_high")
            # CYP substrates
            metab = data.get("metabolism", {})
            if isinstance(metab, dict):
                for key in ("cyp2d6_substrate", "cyp3a4_substrate", "cyp2c9_substrate"):
                    val = metab.get(key)
                    if val is not None:
                        key_results[key] = round(float(val), 3) if isinstance(val, (int, float)) else val

        elif tool_name == "check_compliance":
            overall = data.get("overall_status") or data.get("base_compliance", {}).get("overall_status", "")
            key_results["overall_status"] = overall
            # Count flags
            base = data.get("base_compliance", {})
            if isinstance(base, dict):
                for agent_name, agent_data in base.items():
                    if isinstance(agent_data, dict) and agent_data.get("status") in ("flagged", "blocked", "fail"):
                        flags.append(f"{agent_name}:{agent_data.get('status')}")

        elif tool_name == "calculate_properties":
            for key in ("mw", "logp", "qed", "tpsa", "hbd", "hba", "lipinski_pass", "canonical_smiles"):
                if key in data:
                    val = data[key]
                    key_results[key] = round(float(val), 2) if isinstance(val, float) else val
            if data.get("lipinski_pass") is False:
                flags.append("lipinski_violation")

        elif tool_name == "optimize_molecule":
            variants = data.get("variants", [])
            key_results["variants_count"] = len(variants)
            if variants:
                best_qed = max((v.get("qed", 0) for v in variants if isinstance(v, dict)), default=0)
                key_results["best_qed"] = round(best_qed, 3) if best_qed else None

        return key_results, flags

    async def _enrich_export_data(self, data: Any, source_tool: str) -> Any:
        """
        Auto-enrich sparse export data by re-running the source tool.

        If the user (via Claude) only passed a SMILES string without prediction
        values, re-run the prediction internally and merge the full results.
        The mapping engine then maps these fields to the user's column headers.
        """
        enrichable_tools = {"predict_admet", "get_molecule_profile"}
        if source_tool not in enrichable_tools:
            return data

        # Normalize to list
        is_single = isinstance(data, dict)
        rows = [data] if is_single else (data if isinstance(data, list) else [])
        if not rows:
            return data

        enriched_rows = []
        for row in rows:
            if not isinstance(row, dict):
                enriched_rows.append(row)
                continue

            # Find SMILES in the row (check common key names)
            smiles = None
            for key in ("smiles", "SMILES", "molecule_smiles", "smi", "canonical_smiles"):
                if key in row and isinstance(row[key], str) and len(row[key]) > 5:
                    smiles = row[key]
                    break
            # Fallback: check if any value looks like a SMILES
            if not smiles:
                for v in row.values():
                    if isinstance(v, str) and len(v) > 10 and any(c in v for c in "()=#"):
                        smiles = v
                        break

            if not smiles:
                enriched_rows.append(row)
                continue

            # Count non-audit, non-smiles fields to detect sparse data
            audit_keys = {"_exported_by", "_export_id", "_exported_at"}
            smiles_keys = {"smiles", "SMILES", "molecule_smiles", "smi", "canonical_smiles"}
            data_keys = {k for k in row.keys() if k not in audit_keys and k not in smiles_keys}

            if len(data_keys) >= 5:
                # Data is rich enough — no enrichment needed
                enriched_rows.append(row)
                continue

            # Data is sparse — enrich by re-running the source tool
            logger.info(f"Export data sparse ({len(data_keys)} data fields). "
                        f"Enriching from {source_tool} for SMILES: {smiles[:40]}...")

            try:
                if source_tool == "predict_admet":
                    result = await self._execute_predict_admet({"smiles": smiles})
                elif source_tool == "get_molecule_profile":
                    result = await self._execute_get_molecule_profile({"smiles": smiles})
                else:
                    enriched_rows.append(row)
                    continue

                if result.success and result.data:
                    flat = self._flatten_prediction_data(result.data)
                    # Add convenience aliases for common user column names.
                    # Alias the bare `cardiotoxicity` to the VALIDATED DICTrank head
                    # (cardiotoxicity_dict); the legacy cardiotoxicity_max head is
                    # suppressed upstream and no longer surfaces in `flat`.
                    if "cardiotoxicity_dict" in flat and "cardiotoxicity" not in flat:
                        flat["cardiotoxicity"] = flat["cardiotoxicity_dict"]
                    # Merge: original row values take precedence over enriched
                    merged = {**flat, **row}
                    logger.info(f"Enriched export data: {len(flat)} fields added "
                                f"(total {len(merged)} fields)")
                    enriched_rows.append(merged)
                else:
                    logger.warning(f"Enrichment failed for {source_tool}: {result.error}")
                    enriched_rows.append(row)

            except Exception as e:
                logger.warning(f"Failed to enrich export data: {e}")
                enriched_rows.append(row)

        return enriched_rows[0] if is_single and len(enriched_rows) == 1 else enriched_rows

    async def _execute_export_results(self, arguments: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Export tool results to a configured destination (via novomcp-bridge)."""
        context = context or {}
        org_id = context.get("org_id")
        if not org_id:
            return ToolResult(success=False, error="Organization context required")

        connection_id = arguments.get("connection_id")
        data = arguments.get("data")
        source_tool = arguments.get("source_tool")

        if not all([connection_id, data, source_tool]):
            return ToolResult(success=False, error="connection_id, data, and source_tool are required")

        # Auto-enrich sparse data (e.g. just SMILES) by re-running the source tool
        data = await self._enrich_export_data(data, source_tool)

        try:
            resp = await self.client.post(
                f"{self.bridge_url}/export",
                json={
                    "connection_id": connection_id,
                    "org_id": org_id,
                    "user_id": context.get("user_id", ""),
                    "user_email": context.get("user_email", ""),
                    "user_tier": context.get("user_tier", "free"),
                    "data": data,
                    "source_tool": source_tool,
                    "mapping_id": arguments.get("mapping_id"),
                    "write_mode": arguments.get("write_mode", "append"),
                    "target": arguments.get("target"),
                },
                headers={"X-Bridge-Key": self.bridge_key},
                timeout=120.0,
            )

            if resp.status_code != 200:
                return ToolResult(success=False, error=f"Bridge export failed: HTTP {resp.status_code}")

            result = resp.json()

            if result.get("success"):
                return ToolResult(
                    success=True,
                    data={
                        "status": "success",
                        "rows_written": result.get("rows_written", 0),
                        "rows_failed": result.get("rows_failed", 0),
                        "target_location": result.get("target_location", ""),
                        "details": result.get("details", {}),
                        "message": f"Successfully exported {result.get('rows_written', 0)} rows to {result.get('target_location', '')}",
                    },
                    usage={"tool": "export_results", "rows_exported": result.get("rows_written", 0)},
                )
            else:
                return ToolResult(
                    success=False,
                    error=result.get("error") or "Export failed",
                    data={
                        "rows_written": result.get("rows_written", 0),
                        "rows_failed": result.get("rows_failed", 0),
                        "target_location": result.get("target_location", ""),
                    },
                    usage={"tool": "export_results"},
                )

        except Exception as e:
            logger.error(f"export_results failed: {type(e).__name__}: {e}")
            return ToolResult(success=False, error=f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)

    # -------------------------------------------------------------------------
    # pull_from_source — Bidirectional data pipeline
    # -------------------------------------------------------------------------

    # Standard SMILES column names for auto-detection
    _SMILES_COLUMN_NAMES = {"smiles", "smi", "canonical_smiles", "smiles_string", "molecule", "compound_smiles"}

    @staticmethod
    def _detect_smiles_column(columns: List[str]) -> Optional[str]:
        """Auto-detect the SMILES column from a list of column names."""
        for col in columns:
            if col.lower() in MCPToolExecutor._SMILES_COLUMN_NAMES:
                return col
        return None

    async def _execute_pull_from_source(self, arguments: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Unified pull-from-source tool — dispatches to preview/pull/estimate/execute handlers."""
        user_tier = (context or {}).get("user_tier", "free")
        if user_tier not in ("team", "enterprise"):
            return ToolResult(
                success=False,
                error="Data connectors require the Scale plan ($500/mo). Upgrade at https://novomcp.com/pricing",
            )

        action = arguments.get("action")
        if not action:
            return ToolResult(
                success=False,
                error="'action' is required. Use: preview, pull, estimate_pipeline, or execute_pipeline"
            )

        if action == "preview":
            return await self._execute_pull_preview(arguments, context)
        elif action == "pull":
            return await self._execute_pull_data(arguments, context)
        elif action == "estimate_pipeline":
            return await self._execute_pull_estimate(arguments, context)
        elif action == "execute_pipeline":
            return await self._execute_pull_pipeline(arguments, context)
        else:
            return ToolResult(
                success=False,
                error=f"Unknown action: '{action}'. Valid actions: preview, pull, estimate_pipeline, execute_pipeline"
            )

    async def _execute_pull_preview(self, arguments: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Inspect a source table: columns, row count, sample rows, SMILES column detection."""
        context = context or {}
        org_id = context.get("org_id")
        if not org_id:
            return ToolResult(success=False, error="Organization context required")

        connection_id = arguments.get("connection_id")
        table = arguments.get("table")
        if not connection_id or not table:
            return ToolResult(success=False, error="connection_id and table are required for preview")

        user_tier = context.get("user_tier", "free")

        try:
            # 1. Discover schema (reuse existing bridge endpoint)
            schema_resp = await self.client.post(
                f"{self.bridge_url}/discover-schema",
                json={
                    "connection_id": connection_id,
                    "org_id": org_id,
                    "user_tier": user_tier,
                    "target_filter": table,
                },
                headers={"X-Bridge-Key": self.bridge_key},
                timeout=120.0,
            )

            schema_data = schema_resp.json() if schema_resp.status_code == 200 else {}
            schemas = schema_data.get("schemas", [])
            connector_type = schema_data.get("connector_type", "unknown")

            # 2. Count rows
            count_resp = await self.client.post(
                f"{self.bridge_url}/count-rows",
                json={
                    "connection_id": connection_id,
                    "org_id": org_id,
                    "user_tier": user_tier,
                    "table": table,
                    "filters": self._serialize_filters(arguments.get("filters")),
                },
                headers={"X-Bridge-Key": self.bridge_key},
                timeout=30.0,
            )
            count_data = count_resp.json() if count_resp.status_code == 200 else {}
            total_rows = count_data.get("row_count", 0)

            # 3. Sample rows (limit=5)
            sample_resp = await self.client.post(
                f"{self.bridge_url}/read-data",
                json={
                    "connection_id": connection_id,
                    "org_id": org_id,
                    "user_tier": user_tier,
                    "table": table,
                    "filters": self._serialize_filters(arguments.get("filters")),
                    "limit": 5,
                    "offset": 0,
                },
                headers={"X-Bridge-Key": self.bridge_key},
                timeout=30.0,
            )
            sample_data = sample_resp.json() if sample_resp.status_code == 200 else {}
            from core.prompt_sanitizer import sanitize_rows_for_prompt
            sample_rows = sanitize_rows_for_prompt(sample_data.get("rows", []), max_rows=5)
            columns = sample_data.get("columns", [])

            # 4. Auto-detect SMILES column
            detected_smiles = self._detect_smiles_column(columns)

            # 5. Get tier limits
            max_rows = PULL_ROW_LIMITS.get(user_tier, 50)

            return ToolResult(
                success=True,
                data={
                    "connection_id": connection_id,
                    "connector_type": connector_type,
                    "table": table,
                    "columns": columns,
                    "total_rows": total_rows,
                    "sample_rows": sample_rows,
                    "detected_smiles_column": detected_smiles,
                    "tier_row_limit": max_rows,
                    "schemas": schemas[:3],
                    "message": (
                        f"Table '{table}' has {total_rows} rows and {len(columns)} columns. "
                        f"SMILES column: {detected_smiles or 'not detected (specify smiles_column)'}. "
                        f"Your tier ({user_tier}) allows up to {max_rows} rows per pull."
                    ),
                },
                usage={"tool": "pull_preview"},
            )

        except Exception as e:
            logger.error(f"pull_preview failed: {e}")
            return ToolResult(success=False, error=str(e))

    async def _execute_pull_data(self, arguments: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Pull rows from a source table with constrained filters."""
        context = context or {}
        org_id = context.get("org_id")
        if not org_id:
            return ToolResult(success=False, error="Organization context required")

        connection_id = arguments.get("connection_id")
        table = arguments.get("table")
        if not connection_id or not table:
            return ToolResult(success=False, error="connection_id and table are required for pull")

        user_tier = context.get("user_tier", "free")
        max_rows = PULL_ROW_LIMITS.get(user_tier, 50)
        requested_limit = min(arguments.get("limit", 100), max_rows)

        try:
            resp = await self.client.post(
                f"{self.bridge_url}/read-data",
                json={
                    "connection_id": connection_id,
                    "org_id": org_id,
                    "user_id": context.get("user_id", ""),
                    "user_tier": user_tier,
                    "table": table,
                    "columns": arguments.get("columns"),
                    "filters": self._serialize_filters(arguments.get("filters")),
                    "limit": requested_limit,
                    "offset": arguments.get("offset", 0),
                },
                headers={"X-Bridge-Key": self.bridge_key},
                timeout=120.0,
            )

            if resp.status_code != 200:
                return ToolResult(success=False, error=f"Bridge read-data failed: HTTP {resp.status_code}")

            data = resp.json()

            if not data.get("success"):
                return ToolResult(success=False, error=data.get("error", "Read failed"))

            total = data.get("total_available", 0)
            row_count = data.get("row_count", 0)
            truncated = data.get("truncated", False)

            from core.prompt_sanitizer import sanitize_rows_for_prompt
            result_data = {
                "rows": sanitize_rows_for_prompt(data.get("rows", [])),
                "columns": data.get("columns", []),
                "row_count": row_count,
                "total_available": total,
            }

            if truncated:
                result_data["message"] = (
                    f"Returned {row_count} of {total} total rows. "
                    f"Tier limit: {max_rows}. Use filters to narrow results, "
                    f"or use estimate_pipeline + execute_pipeline for server-side batch processing."
                )

            return ToolResult(
                success=True,
                data=result_data,
                usage={"tool": "pull_data", "rows_pulled": row_count},
            )

        except Exception as e:
            logger.error(f"pull_data failed: {e}")
            return ToolResult(success=False, error=str(e))

    async def _execute_pull_estimate(self, arguments: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Estimate credit cost for a pull→process→push pipeline and return a confirmation token."""
        context = context or {}
        org_id = context.get("org_id")
        if not org_id:
            return ToolResult(success=False, error="Organization context required")

        connection_id = arguments.get("connection_id")
        table = arguments.get("table")
        processing_tools = arguments.get("processing_tools", [])

        if not connection_id or not table:
            return ToolResult(success=False, error="connection_id and table are required")

        if not processing_tools:
            return ToolResult(success=False, error="processing_tools is required (e.g., ['predict_admet', 'check_compliance'])")

        user_tier = context.get("user_tier", "free")
        max_rows = PULL_ROW_LIMITS.get(user_tier, 50)

        try:
            # Count rows with filters
            count_resp = await self.client.post(
                f"{self.bridge_url}/count-rows",
                json={
                    "connection_id": connection_id,
                    "org_id": org_id,
                    "user_tier": user_tier,
                    "table": table,
                    "filters": self._serialize_filters(arguments.get("filters")),
                },
                headers={"X-Bridge-Key": self.bridge_key},
                timeout=30.0,
            )

            if count_resp.status_code != 200:
                return ToolResult(success=False, error="Failed to count source rows")

            total_rows = count_resp.json().get("row_count", 0)
            effective_rows = min(total_rows, max_rows)

            # Calculate credit cost
            pull_cost = TOOL_CREDITS.get("pull_from_source", 5)
            per_row_cost = sum(TOOL_CREDITS.get(t, 0) for t in processing_tools)
            processing_cost = effective_rows * per_row_cost
            push_cost = 5 if arguments.get("destination_connection_id") else 0
            total_cost = pull_cost + processing_cost + push_cost

            # Generate confirmation token (HMAC-based, 10-min TTL)
            import hashlib

            token_data = {
                "org_id": org_id,
                "connection_id": connection_id,
                "table": table,
                "rows": effective_rows,
                "tools": processing_tools,
                "cost": total_cost,
                "ts": int(time.time()),
            }
            token_str = json.dumps(token_data, sort_keys=True)
            token_hash = hashlib.sha256(
                f"{token_str}:{self.internal_api_key}".encode()
            ).hexdigest()[:32]
            confirmation_token = f"pipe_{token_hash}"

            await self._store_token(confirmation_token, token_data)

            return ToolResult(
                success=True,
                data={
                    "pipeline_estimate": {
                        "source_connection": connection_id,
                        "source_table": table,
                        "total_source_rows": total_rows,
                        "rows_to_process": effective_rows,
                        "tier_row_limit": max_rows,
                        "processing_tools": processing_tools,
                        "destination": arguments.get("destination_connection_id"),
                        "destination_table": arguments.get("destination_table"),
                    },
                    "credit_breakdown": {
                        "pull_cost": pull_cost,
                        "per_row_tool_cost": per_row_cost,
                        "processing_cost": processing_cost,
                        "push_cost": push_cost,
                        "total_credits": total_cost,
                    },
                    "confirmation_token": confirmation_token,
                    "token_expires_in_seconds": 600,
                    "message": (
                        f"Pipeline will pull {effective_rows} rows from '{table}', "
                        f"run {', '.join(processing_tools)} on each row "
                        f"({per_row_cost} credits/row), "
                        f"{'and push to destination ' if push_cost else ''}"
                        f"for a total of {total_cost} credits. "
                        f"Use execute_pipeline with this confirmation_token to proceed."
                    ),
                },
                usage={"tool": "pull_estimate"},
            )

        except Exception as e:
            logger.error(f"pull_estimate failed: {e}")
            return ToolResult(success=False, error=str(e))

    async def _execute_pull_pipeline(self, arguments: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Execute a confirmed pull→process→push pipeline."""
        import time
        import uuid

        context = context or {}
        org_id = context.get("org_id")
        if not org_id:
            return ToolResult(success=False, error="Organization context required")

        confirmation_token = arguments.get("confirmation_token")
        if not confirmation_token:
            return ToolResult(
                success=False,
                error="confirmation_token is required. Use estimate_pipeline first to get a token."
            )

        # Validate token
        token_data = await self._get_token(confirmation_token)
        if not token_data:
            return ToolResult(success=False, error="Invalid or expired confirmation token. Run estimate_pipeline again.")

        if token_data.get("org_id") != org_id:
            return ToolResult(success=False, error="Token organization mismatch")

        # Mark token as used before execution (prevents replay)
        await self._mark_token_used(confirmation_token)

        pipeline_id = f"pipe_{uuid.uuid4().hex[:16]}"
        connection_id = token_data["connection_id"]
        table = token_data["table"]
        effective_rows = token_data["rows"]
        processing_tools = token_data["tools"]
        total_cost = token_data["cost"]
        user_tier = context.get("user_tier", "free")

        smiles_column = arguments.get("smiles_column")
        dest_connection_id = arguments.get("destination_connection_id")
        dest_table = arguments.get("destination_table")

        rows_processed = 0
        processing_errors = 0
        rows_pushed = 0

        try:
            # 1. Pull data from source
            pull_resp = await self.client.post(
                f"{self.bridge_url}/read-data",
                json={
                    "connection_id": connection_id,
                    "org_id": org_id,
                    "user_tier": user_tier,
                    "table": table,
                    "columns": arguments.get("columns"),
                    "filters": self._serialize_filters(arguments.get("filters")),
                    "limit": effective_rows,
                    "offset": 0,
                },
                headers={"X-Bridge-Key": self.bridge_key},
                timeout=300.0,
            )

            if pull_resp.status_code != 200:
                return ToolResult(success=False, error=f"Failed to pull data: HTTP {pull_resp.status_code}")

            pull_data = pull_resp.json()
            if not pull_data.get("success"):
                return ToolResult(success=False, error=pull_data.get("error", "Pull failed"))

            rows = pull_data.get("rows", [])
            columns = pull_data.get("columns", [])

            if not rows:
                return ToolResult(success=False, error="No rows returned from source")

            # 2. Detect SMILES column
            if not smiles_column:
                smiles_column = self._detect_smiles_column(columns)
            if not smiles_column:
                return ToolResult(
                    success=False,
                    error=f"Could not auto-detect SMILES column. Columns: {columns}. Specify smiles_column parameter."
                )

            # 3. Process each row with per-molecule audit logging
            enriched_rows = []
            molecule_audit_log = []

            for i, row in enumerate(rows):
                smiles = row.get(smiles_column)

                audit_entry = {
                    "row_index": i,
                    "input_smiles": smiles if isinstance(smiles, str) else None,
                    "canonical_smiles": None,
                    "standardization": "none",
                    "valid": True,
                    "tools_applied": {},
                    "disposition": "included",
                    "exclusion_reason": None,
                }

                if not smiles or not isinstance(smiles, str):
                    audit_entry["valid"] = False
                    audit_entry["disposition"] = "excluded"
                    audit_entry["exclusion_reason"] = "invalid_smiles" if smiles else "missing_smiles"
                    enriched_rows.append(row)
                    processing_errors += 1
                    molecule_audit_log.append(audit_entry)
                    continue

                enriched_row = dict(row)
                row_excluded = False

                for tool_name in processing_tools:
                    try:
                        handler = getattr(self, f"_execute_{tool_name}", None)
                        if not handler:
                            audit_entry["tools_applied"][tool_name] = {"status": "skipped", "reason": "no_handler"}
                            continue

                        result = await handler({"smiles": smiles})

                        if result.success and result.data:
                            flat = self._flatten_prediction_data(result.data)
                            for k, v in flat.items():
                                enriched_row[f"{tool_name}_{k}"] = v

                            # Extract key results for audit
                            key_results, flags = self._extract_audit_summary(tool_name, result.data)
                            audit_entry["tools_applied"][tool_name] = {
                                "status": "success",
                                "key_results": key_results,
                                "flags": flags,
                            }

                            # Check for compliance blocks
                            if tool_name == "check_compliance" and result.data:
                                overall = result.data.get("overall_status") or result.data.get("base_compliance", {}).get("overall_status", "")
                                if overall.lower() in ("blocked", "block", "fail"):
                                    row_excluded = True
                                    audit_entry["disposition"] = "excluded"
                                    blocked_by = flags[0] if flags else "compliance_block"
                                    audit_entry["exclusion_reason"] = f"compliance_block:{blocked_by}"
                        else:
                            audit_entry["tools_applied"][tool_name] = {
                                "status": "error",
                                "error": result.error or "Unknown error",
                            }
                            processing_errors += 1

                    except Exception as e:
                        logger.warning(f"Pipeline processing error ({tool_name}): {e}")
                        audit_entry["tools_applied"][tool_name] = {
                            "status": "error",
                            "error": str(e),
                        }
                        processing_errors += 1

                # Store canonical SMILES if properties were computed
                if "calculate_properties" in audit_entry["tools_applied"]:
                    canon = enriched_row.get("calculate_properties_canonical_smiles")
                    if canon:
                        audit_entry["canonical_smiles"] = canon
                        if canon != smiles:
                            audit_entry["standardization"] = "canonicalized"

                if not row_excluded:
                    audit_entry["disposition"] = "included"

                enriched_rows.append(enriched_row)
                rows_processed += 1
                molecule_audit_log.append(audit_entry)

            # 4. Push to destination (optional)
            if dest_connection_id and enriched_rows:
                try:
                    push_resp = await self.client.post(
                        f"{self.bridge_url}/export",
                        json={
                            "connection_id": dest_connection_id,
                            "org_id": org_id,
                            "user_id": context.get("user_id", ""),
                            "user_email": context.get("user_email", ""),
                            "user_tier": user_tier,
                            "data": enriched_rows,
                            "source_tool": "pull_from_source",
                            "write_mode": "append",
                            "target": dest_table,
                        },
                        headers={"X-Bridge-Key": self.bridge_key},
                        timeout=300.0,
                    )
                    if push_resp.status_code == 200:
                        push_data = push_resp.json()
                        rows_pushed = push_data.get("rows_written", 0)
                except Exception as e:
                    logger.error(f"Pipeline push failed: {e}")

            # 5. Compute audit summary
            audit_summary = {
                "total": len(molecule_audit_log),
                "included": sum(1 for a in molecule_audit_log if a["disposition"] == "included"),
                "excluded": sum(1 for a in molecule_audit_log if a["disposition"] == "excluded"),
                "invalid_smiles": sum(1 for a in molecule_audit_log if not a["valid"]),
                "compliance_blocks": sum(1 for a in molecule_audit_log if a.get("exclusion_reason", "").startswith("compliance_block")),
                "processing_errors": processing_errors,
            }

            # 6. Record audit trail (with per-molecule log)
            try:
                await self.client.post(
                    f"{self.dashboard_url}/mcp/record-pipeline",
                    json={
                        "pipeline_id": pipeline_id,
                        "org_id": org_id,
                        "user_id": context.get("user_id"),
                        "source_connection_id": connection_id,
                        "source_table": table,
                        "rows_pulled": len(rows),
                        "processing_tools": ",".join(processing_tools),
                        "rows_processed": rows_processed,
                        "processing_errors": processing_errors,
                        "dest_connection_id": dest_connection_id,
                        "dest_table": dest_table,
                        "rows_pushed": rows_pushed,
                        "total_credits": total_cost,
                        "status": "completed",
                        "molecule_audit_log": molecule_audit_log,
                        "audit_summary": audit_summary,
                    },
                    headers={"X-API-Key": self.dashboard_api_key},
                    timeout=30.0,
                )
            except Exception as e:
                logger.warning(f"Failed to record pipeline audit: {e}")

            # 7. Build response
            result_data = {
                "pipeline_id": pipeline_id,
                "status": "completed",
                "source": {"connection_id": connection_id, "table": table},
                "rows_pulled": len(rows),
                "rows_processed": rows_processed,
                "processing_errors": processing_errors,
                "processing_tools": processing_tools,
                "credits_consumed": total_cost,
                "audit_summary": audit_summary,
                "molecule_audit_log": molecule_audit_log,
            }

            if dest_connection_id:
                result_data["destination"] = {
                    "connection_id": dest_connection_id,
                    "table": dest_table,
                    "rows_pushed": rows_pushed,
                }
            else:
                # Return enriched data to Claude for small datasets
                result_data["enriched_rows"] = enriched_rows

            result_data["message"] = (
                f"Pipeline {pipeline_id}: pulled {len(rows)} rows, "
                f"processed {rows_processed} "
                f"({audit_summary['included']} included, {audit_summary['excluded']} excluded, "
                f"{processing_errors} errors). "
                f"{'Pushed ' + str(rows_pushed) + ' rows to destination. ' if dest_connection_id else 'Data returned inline. '}"
                f"Credits: {total_cost}."
            )

            return ToolResult(
                success=True,
                data=result_data,
                usage={"tool": "pull_pipeline", "credits": total_cost, "rows": len(rows)},
            )

        except Exception as e:
            # Record failed pipeline
            try:
                await self.client.post(
                    f"{self.dashboard_url}/mcp/record-pipeline",
                    json={
                        "pipeline_id": pipeline_id,
                        "org_id": org_id,
                        "user_id": context.get("user_id"),
                        "source_connection_id": connection_id,
                        "source_table": table,
                        "rows_pulled": 0,
                        "processing_tools": ",".join(processing_tools),
                        "rows_processed": rows_processed,
                        "processing_errors": processing_errors,
                        "total_credits": 0,
                        "status": "failed",
                        "error_message": str(e)[:500],
                    },
                    headers={"X-API-Key": self.dashboard_api_key},
                    timeout=10.0,
                )
            except Exception:
                pass
            logger.error(f"pull_pipeline failed: {e}")
            return ToolResult(success=False, error=str(e))

    @staticmethod
    def _serialize_filters(filters: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
        """Ensure filters are serializable dicts for bridge API calls."""
        if not filters:
            return None
        return [
            {"column": f.get("column", ""), "operator": f.get("operator", "="), "value": f.get("value")}
            for f in filters
        ]

    # =========================================================================
    # Omics Tool Executors (target_discovery, stratify_patients)
    # =========================================================================

    async def _execute_target_discovery(self, args: Dict[str, Any]) -> ToolResult:
        """Discover drug targets for a disease using pre-computed omics evidence.

        Fires background GPU warmup pings — target_discovery is the natural funnel
        entry point, and Steps 6-7 will need docking/MD 20-40 min from now. By then
        containers will be warm. Fire-and-forget; never blocks the handler.
        """
        asyncio.create_task(self._warmup_gpu_services())

        disease = args.get("disease")
        if not disease:
            return ToolResult(success=False, error="Missing required parameter: disease")

        min_evidence = args.get("min_evidence", 0.5)
        max_targets = min(args.get("max_targets", 10), 50)

        try:
            # Resolve disease name to EFO ID if needed
            disease_efo_id = disease if disease.startswith("EFO_") or disease.startswith("MONDO_") else None

            if not disease_efo_id:
                # Query Open Targets search API to resolve disease name → EFO ID
                async with httpx.AsyncClient(timeout=15.0) as ot_client:
                    resp = await ot_client.post(
                        "https://api.platform.opentargets.org/api/v4/graphql",
                        json={
                            "query": """query($q: String!) {
                                search(queryString: $q, entityNames: ["disease"], page: {size: 5, index: 0}) {
                                    hits { id name }
                                }
                            }""",
                            "variables": {"q": disease}
                        }
                    )
                    if resp.status_code == 200:
                        hits = resp.json().get("data", {}).get("search", {}).get("hits", [])
                        if hits:
                            disease_efo_id = hits[0]["id"]
                            disease_name = hits[0].get("name", disease)
                        else:
                            return ToolResult(success=False, error=f"Disease '{disease}' not found in Open Targets. Try an EFO ID directly.")
                    else:
                        return ToolResult(success=False, error=f"Open Targets search failed: {resp.status_code}")
            else:
                disease_name = disease

            # Query omics_targets in Aurora (migrated from Cosmos 2026-06-01).
            # The composite index ix_omics_targets_disease orders by
            # (disease_efo_id, composite_score DESC, overall_score DESC), so
            # the multi-field ORDER BY that Cosmos quietly dropped is honored
            # server-side here — no client-side sort needed.
            #
            # ---- Perturbation evidence channel (Phase 0, T1-C) ----
            # The perturbation channel adds a fifth weighted signal on top of
            # the 4-channel composite already encoded in omics_targets
            # (Open Targets genetic/overall, ChEMBL, literature, omics
            # expression). It is a Python re-weight, not a SQL composite
            # update: we over-fetch from both tables, left-join in Python on
            # gene_symbol, and re-sort by the extended composite. This lets
            # genes with strong perturbation evidence but moderate base score
            # surface (and vice-versa) without rewriting the precomputed
            # composite at load time.
            #
            # Over-fetch sizing: PERT_TOPK (default 2000) for the perturbation
            # channel and 4x max_targets for omics_targets. Both are bounded
            # by the composite index range scans, so the cost is sub-50ms in
            # practice. PERTURBATION_CHANNEL_ENABLED=false (default true)
            # disables the channel entirely -- T1-D toggle for the eval
            # harness's channel-on/off comparison.
            from core.db_helper import query_sql

            pert_enabled = (
                os.getenv("PERTURBATION_CHANNEL_ENABLED", "true").lower()
                in {"1", "true", "yes", "on"}
            )
            pert_topk = int(os.getenv("PERTURBATION_TOPK", "2000"))
            base_topk = max(max_targets * 4, 50)
            # Channel weight (parity with the omics channel per locked decision).
            PERT_CHANNEL_WEIGHT = 1.0

            async def _q_omics_targets():
                rows = await query_sql(
                    """
                    SELECT id, gene_symbol, ensembl_id, uniprot_id, disease_efo_id,
                           disease_name, composite_score, overall_score,
                           genetic_score, expression_score, best_pdb_resolution_a,
                           pdb_ids, key_variants, top_pathways, suggested_pdb_id,
                           pdb_selection_criteria, known_drugs_count,
                           tractability_small_molecule, source_version
                      FROM omics.omics_targets
                     WHERE disease_efo_id = %s
                       AND overall_score >= %s
                     ORDER BY composite_score DESC NULLS LAST,
                              overall_score DESC NULLS LAST
                     LIMIT %s
                    """,
                    (disease_efo_id, min_evidence, base_topk),
                    database="research-db",
                )
                return rows

            async def _q_perturbation():
                """Top-N (most-reversing first) perturbation rows for the
                disease. We collapse to one row per gene downstream, picking
                the dataset with the most-negative (strongest-evidence) score.
                """
                if not pert_enabled:
                    return []
                try:
                    return await query_sql(
                        """
                        SELECT gene_symbol, dataset_source, perturbation_score,
                               n_overlap_up, n_overlap_down,
                               signature_version, perturbation_data_version,
                               license_tag
                          FROM omics.omics_perturbation
                         WHERE disease_efo_id = %s
                         ORDER BY perturbation_score ASC
                         LIMIT %s
                        """,
                        (disease_efo_id, pert_topk),
                        database="research-db",
                    )
                except Exception as pert_err:
                    # Channel failure must not break the tool — degrade to
                    # the 4-channel ranking. Per-target status will reflect
                    # the degraded coverage downstream (FM 13).
                    logger.warning(
                        "perturbation channel query failed (disease=%s): %s",
                        disease_efo_id, pert_err,
                    )
                    return []

            raw_items, pert_rows = await asyncio.gather(
                _q_omics_targets(), _q_perturbation(),
            )

            # Aurora returns column name `best_pdb_resolution_a`; preserve the
            # legacy `best_pdb_resolution_A` key the response shape uses by
            # renaming on the fly.
            for r in raw_items:
                if "best_pdb_resolution_a" in r:
                    r["best_pdb_resolution_A"] = r.pop("best_pdb_resolution_a")

            # ---- Build per-gene best perturbation row ----
            # `pert_rows` is sorted by perturbation_score ASC, so the first
            # row per gene IS the most-reversing dataset's row.
            pert_by_gene: Dict[str, Dict[str, Any]] = {}
            for r in pert_rows:
                gene = (r.get("gene_symbol") or "").upper()
                if not gene or gene in pert_by_gene:
                    continue
                pert_by_gene[gene] = r

            # Channel coverage: if zero rows came back, every target gets
            # degraded_no_coverage status. This is FM 13 in the roadmap --
            # explicit, surfaced in the audit trail, never silently failing.
            channel_has_coverage = bool(pert_by_gene)

            # ---- Assemble the candidate set (omics_targets ∪ pert-only) ----
            items = list(raw_items)
            seen_ids = {it.get("id") for it in items if it.get("id")}
            seen_genes = {
                (it.get("gene_symbol") or "").upper() for it in items
            }
            # Genes present in the perturbation channel but absent from
            # omics_targets are added as perturbation-only candidates so the
            # channel can surface literature-underweighted targets. They
            # carry zero base-composite signal and float on the perturbation
            # contribution alone.
            for gene, prow in pert_by_gene.items():
                if gene in seen_genes:
                    continue
                synth_id = f"{gene}::{disease_efo_id}"
                if synth_id in seen_ids:
                    continue
                items.append({
                    "id": synth_id,
                    "gene_symbol": gene,
                    "ensembl_id": "",
                    "uniprot_id": "",
                    "disease_efo_id": disease_efo_id,
                    "disease_name": disease_name,
                    "composite_score": 0.0,
                    "overall_score": 0.0,
                    "genetic_score": 0,
                    "expression_score": 0,
                    "best_pdb_resolution_A": None,
                    "pdb_ids": [],
                    "key_variants": [],
                    "top_pathways": [],
                    "suggested_pdb_id": None,
                    "pdb_selection_criteria": None,
                    "known_drugs_count": 0,
                    "tractability_small_molecule": False,
                    "source_version": None,
                    "_perturbation_only": True,
                })
                seen_genes.add(gene)
                seen_ids.add(synth_id)

            if not items:
                return ToolResult(
                    success=True,
                    data={
                        "disease": disease_name,
                        "disease_efo_id": disease_efo_id,
                        "targets": [],
                        "perturbation_channel_enabled": pert_enabled,
                        "perturbation_channel_coverage": channel_has_coverage,
                        "message": f"No targets found for '{disease_name}' with evidence score >= {min_evidence}. Try lowering min_evidence.",
                    },
                    usage={"queries": 1 + (1 if pert_enabled else 0), "tool": "target_discovery"}
                )

            # Build ranked target list with per-channel evidence breakdown.
            targets = []
            for item in items:
                pdb_ids = item.get("pdb_ids", [])
                gene = (item.get("gene_symbol") or "").upper()

                # ---- Perturbation contribution ----
                # Locked transform: contribution = max(0, -perturbation_score)
                # Rationale: perturbation_score in [-1, +1] is signed by
                # convention (negative = perturbation REVERSES disease
                # signature = HIGH evidence). We map most-negative scores to
                # the largest positive contribution. Amplifying perturbations
                # (positive scores) contribute 0 -- they must NOT subtract
                # from a target's composite. If you find yourself wanting to
                # use the raw signed score here, stop -- surface the issue
                # instead of silently inverting the channel.
                prow = pert_by_gene.get(gene)
                if not pert_enabled:
                    pert_block = {
                        "status": "disabled",
                        "score": None,
                        "dataset": None,
                        "signature_version": None,
                        "perturbation_data_version": None,
                        "license_tag": None,
                        "contribution": 0.0,
                        "weight": PERT_CHANNEL_WEIGHT,
                    }
                    pert_contribution = 0.0
                elif not channel_has_coverage:
                    # No rows at all for this disease -- the whole channel is
                    # degraded. FM 13: surface it, do not silently fail.
                    pert_block = {
                        "status": "degraded_no_coverage",
                        "score": None,
                        "dataset": None,
                        "signature_version": None,
                        "perturbation_data_version": None,
                        "license_tag": None,
                        "contribution": 0.0,
                        "weight": PERT_CHANNEL_WEIGHT,
                    }
                    pert_contribution = 0.0
                elif prow is None:
                    # Channel has rows for this disease but not for this
                    # gene -- treat as zero evidence (no boost, no penalty).
                    pert_block = {
                        "status": "ok_no_gene_coverage",
                        "score": None,
                        "dataset": None,
                        "signature_version": None,
                        "perturbation_data_version": None,
                        "license_tag": None,
                        "contribution": 0.0,
                        "weight": PERT_CHANNEL_WEIGHT,
                    }
                    pert_contribution = 0.0
                else:
                    raw_score = float(prow.get("perturbation_score") or 0.0)
                    pert_contribution = max(0.0, -raw_score)
                    pert_block = {
                        "status": "ok",
                        "score": raw_score,
                        "dataset": prow.get("dataset_source"),
                        "signature_version": prow.get("signature_version"),
                        "perturbation_data_version": prow.get("perturbation_data_version"),
                        "license_tag": prow.get("license_tag"),
                        "n_overlap_up": prow.get("n_overlap_up"),
                        "n_overlap_down": prow.get("n_overlap_down"),
                        "contribution": pert_contribution,
                        "weight": PERT_CHANNEL_WEIGHT,
                    }

                # Base composite as precomputed by omics-pipeline phase_b.
                # The omics-pipeline composite already folds the 4 base
                # channels (Open Targets genetic/overall, ChEMBL known-drugs,
                # literature, omics expression) into a single number; we do
                # not break it apart here -- doing so would require a parallel
                # rewrite of phase_b. Per-channel contributions for those 4
                # are surfaced from the row's component scores so the
                # audit-trail story stays inspectable.
                try:
                    base_composite = float(item.get("composite_score") or 0.0)
                except (TypeError, ValueError):
                    base_composite = 0.0

                extended_composite = base_composite + PERT_CHANNEL_WEIGHT * pert_contribution

                evidence_channels = {
                    "opentargets_genetic": item.get("genetic_score", 0),
                    "opentargets_overall": item.get("overall_score", 0),
                    "chembl_known_drugs": item.get("known_drugs_count", 0),
                    "literature": None,   # rolled into composite by omics-pipeline; not separable here
                    "omics_expression": item.get("expression_score", 0),
                    "perturbation": pert_block,
                }

                target = {
                    "gene_symbol": item.get("gene_symbol", ""),
                    "ensembl_id": item.get("ensembl_id", ""),
                    "uniprot_id": item.get("uniprot_id", ""),
                    "overall_score": item.get("overall_score", 0),
                    "composite_score": extended_composite,
                    "base_composite_score": base_composite,
                    "perturbation_contribution": pert_contribution,
                    "genetic_score": item.get("genetic_score", 0),
                    "expression_score": item.get("expression_score", 0),
                    "tractability_small_molecule": item.get("tractability_small_molecule", False),
                    "known_drugs_count": item.get("known_drugs_count", 0),
                    "high_competition": (item.get("known_drugs_count", 0) or 0) > 5,
                    "pdb_ids": pdb_ids[:5] if isinstance(pdb_ids, list) else [],
                    "suggested_pdb_id": item.get("suggested_pdb_id"),
                    "pdb_selection_criteria": item.get("pdb_selection_criteria"),
                    "best_pdb_resolution_A": item.get("best_pdb_resolution_A"),
                    "top_pathways": item.get("top_pathways", []) or [],
                    "has_structure": bool(pdb_ids),
                    "structure_unavailable": item.get("suggested_pdb_id") is None,
                    "perturbation_only": bool(item.get("_perturbation_only")),
                    "evidence_channels": evidence_channels,
                }
                targets.append(target)

            # Python re-sort by the extended composite (perturbation channel
            # may have promoted under-ranked genes / demoted crowded weak
            # ones). Tie-break on overall_score, then gene_symbol for
            # deterministic ordering.
            targets.sort(
                key=lambda t: (
                    -(float(t.get("composite_score") or 0.0)),
                    -(float(t.get("overall_score") or 0.0)),
                    t.get("gene_symbol") or "",
                )
            )

            # Filter perturbation-only candidates whose contribution is
            # zero -- they only made it into the candidate set because the
            # channel had a row, but the score was non-reversing. They carry
            # no signal in either direction; drop them rather than diluting
            # the response with zero-evidence rows.
            targets = [
                t for t in targets
                if not (t.get("perturbation_only") and (t.get("perturbation_contribution") or 0.0) <= 0.0)
            ]

            # Apply the user-requested max_targets cap AFTER the channel
            # blend + re-sort so the top-K reflects the extended ranking.
            targets = targets[:max_targets]

            # Separate dockable targets (validated suggested_pdb_id) from others
            dockable = [t for t in targets if not t["structure_unavailable"]]
            not_dockable = [t for t in targets if t["structure_unavailable"]]

            # Suggested top target must have a validated PDB for docking
            suggested = dockable[0] if dockable else (targets[0] if targets else None)

            return ToolResult(
                success=True,
                data={
                    "disease": disease_name,
                    "disease_efo_id": disease_efo_id,
                    "total_targets": len(targets),
                    "targets_dockable": len(dockable),
                    "targets": dockable + not_dockable,
                    "suggested_target": suggested["gene_symbol"] if suggested else None,
                    "suggested_pdb_id": suggested.get("suggested_pdb_id") if suggested else None,
                    "perturbation_channel_enabled": pert_enabled,
                    "perturbation_channel_coverage": channel_has_coverage,
                    "perturbation_channel_weight": PERT_CHANNEL_WEIGHT,
                    "tool_suggestions": [
                        self._tool_suggestion("search_literature",
                            f"Search literature for {suggested['gene_symbol']} inhibitors in {disease_name}" if suggested else "Search literature for target"),
                        self._tool_suggestion("search_chembl",
                            f"Find known compounds targeting {suggested['gene_symbol']}" if suggested else "Find known compounds"),
                    ]
                },
                usage={"queries": 1 + (1 if pert_enabled else 0), "tool": "target_discovery"}
            )

        except Exception as e:
            logger.exception(f"Error in target_discovery: {e}")
            return ToolResult(success=False, error=f"Target discovery failed: {str(e)}")

    async def _execute_validate_target(self, args: Dict[str, Any]) -> ToolResult:
        """Adversarial target validation — stress-test a target hypothesis against evidence.

        Sub-call results are cached in Redis for 1 hour per (target, disease) pair
        to eliminate run-to-run variance from non-deterministic upstream APIs
        (ChEMBL timeouts, ClinicalTrials.gov pagination). Without the cache, the
        same EGFR/NSCLC query can score 0.54 when ChEMBL is up (25 activities) or
        0.04 when ChEMBL times out (0 activities) — a UX-breaking variance that
        makes the tool unreliable for its core purpose.
        """
        import asyncio

        target = args.get("target", "").strip()
        disease = args.get("disease", "").strip()
        if not target or not disease:
            return ToolResult(success=False, error="Missing required parameters: target, disease")

        # Optional cache bypass — useful when re-running after a server-side
        # tuning change (e.g. relevance-threshold bump) so the test actually
        # exercises the fresh pipeline rather than serving the pre-fix result
        # from the 1-hour TTL.
        skip_cache = bool(args.get("skip_cache", False))

        try:
            # === Cache check (1-hour TTL) ===
            # Key on normalized (target, disease) so repeated calls return identical data.
            cache_key = f"{self._redis_prefix}:validate_target:{target.upper()}:{disease.lower().strip()}"
            r = await self._get_redis()
            if r and not skip_cache:
                try:
                    cached = await r.get(cache_key)
                    if cached:
                        cached_data = json.loads(cached)
                        cached_data["_cached"] = True
                        return ToolResult(
                            success=True,
                            data=cached_data,
                            usage={"queries": 0, "tool": "validate_target", "source": "cache"}
                        )
                except Exception:
                    pass  # Cache miss or parse error — proceed with fresh call

            # === Phase 1: Gather evidence in parallel ===
            async def get_omics():
                """Get omics evidence from target_discovery."""
                try:
                    result = await self._execute_target_discovery({
                        "disease": disease, "max_targets": 30, "min_evidence": 0.3
                    })
                    if result.success and result.data:
                        for t in result.data.get("targets", []):
                            if t.get("gene_symbol", "").upper() == target.upper():
                                return t
                    return None
                except Exception:
                    return None

            async def get_completed_trials():
                """Search for completed clinical trials (supporting evidence). Returns (items, total_count)."""
                try:
                    result = await self._execute_search_clinical_trials({
                        "query": f"{target} {disease}",
                        "status": "COMPLETED",
                        "top_k": 10
                    })
                    if result.success and result.data:
                        return result.data.get("trials", []), result.data.get("total_count", 0)
                    return [], 0
                except Exception:
                    return [], 0

            async def get_terminated_trials():
                """Search for terminated/failed trials (contradicting evidence). Returns (items, total_count)."""
                try:
                    result = await self._execute_search_clinical_trials({
                        "query": f"{target} {disease}",
                        "status": "TERMINATED",
                        "top_k": 10
                    })
                    if result.success and result.data:
                        return result.data.get("trials", []), result.data.get("total_count", 0)
                    return [], 0
                except Exception:
                    return [], 0

            # Relevance gate for semantic literature hits.
            #
            # The 14.4K-paper Pinecone index always returns top-K matches per
            # query (default 20 supporting + 15 contradicting). Without a
            # similarity threshold, every target — including fabricated genes
            # like "OBSCURE_GENE" — gets the same saturated 20/15 count, plus
            # a "20 supporting publications" strength line listing generic
            # hits ("CRISPR/Cas gene therapy", "Engineering Liver-Specific
            # Promoters") that have nothing to do with the target. The
            # literature stream then becomes effectively dead weight in the
            # confidence score and misleading in the strengths/risks list.
            #
            # text-embedding-3-large similarity on this corpus, calibrated
            # against probes 2026-06-09:
            #
            #   Real, comfortably above the floor (EGFR + lung cancer):  0.69 / 0.66 / 0.63
            #   Real, near the lower edge of legitimate signal (BRAF + melanoma):  0.57 / 0.56
            #   Fictional (random invented gene + disease):              0.49 / 0.48 / 0.48
            #
            # The signal/noise gap is clean — real targets sit at 0.56+; generic
            # drug-discovery papers that surface for nonsense queries top out
            # around ~0.50. Initial 0.40 default sat below the noise
            # floor — caught the contradicting stream (15→4) but left supporting
            # saturated at 20 for fictional targets. New default 0.55 splits the
            # gap: fictional targets drop to ~0 supporting papers, real targets
            # retain their hits. BRAF sits closest to the floor (top hits at
            # 0.57/0.56) — if a future re-probe shows the corpus or embedding
            # has drifted such that BRAF starts losing its top supporting hits,
            # that's the early-warning sign to re-calibrate (probably down to
            # ~0.50). Env-tunable: VALIDATE_TARGET_LIT_MIN_SCORE.
            LIT_MIN_SCORE = float(os.getenv("VALIDATE_TARGET_LIT_MIN_SCORE", "0.55"))

            def _filter_relevant(papers):
                """Keep only papers whose semantic similarity exceeds the
                relevance gate. Papers without a usable score (rare; older
                index entries) fall back to inclusion so we don't silently
                discard data on indexing drift."""
                kept = []
                for p in papers:
                    try:
                        score = float(p.get("score") or 0.0)
                    except (TypeError, ValueError):
                        score = 0.0
                    if score == 0.0 and p.get("score") is None:
                        # No score recorded — keep it (legacy index entries)
                        kept.append(p)
                    elif score >= LIT_MIN_SCORE:
                        kept.append(p)
                return kept

            async def get_supporting_literature():
                """Search for supporting literature, relevance-gated."""
                try:
                    result = await self._execute_search_literature({
                        "query": f"{target} {disease} therapeutic target",
                        "top_k": 20
                    })
                    papers = result.data.get("papers", []) if result.success and result.data else []
                    return _filter_relevant(papers)
                except Exception:
                    return []

            async def get_contradicting_literature():
                """Search for contradicting literature (failure signals), relevance-gated."""
                try:
                    result = await self._execute_search_literature({
                        "query": f"{target} resistance failure toxicity ineffective",
                        "top_k": 15
                    })
                    papers = result.data.get("papers", []) if result.success and result.data else []
                    return _filter_relevant(papers)
                except Exception:
                    return []

            async def get_chembl_activity():
                """Get ChEMBL bioactivity data."""
                try:
                    result = await self._execute_search_chembl({
                        "query": target,
                        "search_type": "activity",
                        "top_k": 25
                    })
                    return result.data.get("results", []) if result.success and result.data else []
                except Exception:
                    return []

            async def get_perturbation():
                """Get perturbation evidence for (target, disease) from
                omics.omics_perturbation. Returns the single most-reversing
                row across datasets (MIN(perturbation_score)) so the
                validate_target tier reflects the strongest available signal.

                Disease resolution: validate_target accepts free-form disease
                strings. We try the disease string directly as an EFO/MONDO
                id first; if it doesn't start with the canonical prefix we
                resolve it via Open Targets search (same path target_discovery
                uses). On any failure -> degraded_no_coverage, never raise.

                PERTURBATION_CHANNEL_ENABLED=false hides the channel entirely.
                """
                pert_enabled_local = (
                    os.getenv("PERTURBATION_CHANNEL_ENABLED", "true").lower()
                    in {"1", "true", "yes", "on"}
                )
                if not pert_enabled_local:
                    return {"_status": "disabled"}
                try:
                    # Resolve disease -> EFO/MONDO id.
                    if disease.startswith("EFO_") or disease.startswith("MONDO_"):
                        disease_efo_id_local = disease
                    else:
                        async with httpx.AsyncClient(timeout=15.0) as ot_client:
                            resp = await ot_client.post(
                                "https://api.platform.opentargets.org/api/v4/graphql",
                                json={
                                    "query": """query($q: String!) {
                                        search(queryString: $q, entityNames: ["disease"], page: {size: 1, index: 0}) {
                                            hits { id name }
                                        }
                                    }""",
                                    "variables": {"q": disease}
                                }
                            )
                            if resp.status_code != 200:
                                return {"_status": "degraded_no_coverage"}
                            hits = resp.json().get("data", {}).get("search", {}).get("hits", [])
                            if not hits:
                                return {"_status": "degraded_no_coverage"}
                            disease_efo_id_local = hits[0]["id"]

                    from core.db_helper import query_sql
                    rows = await query_sql(
                        """
                        SELECT gene_symbol, dataset_source, perturbation_score,
                               n_overlap_up, n_overlap_down,
                               signature_version, perturbation_data_version,
                               license_tag
                          FROM omics.omics_perturbation
                         WHERE gene_symbol = %s
                           AND disease_efo_id = %s
                         ORDER BY perturbation_score ASC
                         LIMIT 1
                        """,
                        (target.upper(), disease_efo_id_local),
                        database="research-db",
                    )
                    if not rows:
                        # Check whether the disease has any coverage at all
                        # so we can distinguish "no rows for this gene" from
                        # "no rows for the whole disease" (both surface as
                        # degraded_no_coverage at this layer -- the tier
                        # contribution is 0 in either case).
                        return {"_status": "degraded_no_coverage",
                                "_disease_efo_id": disease_efo_id_local}
                    return {"_status": "ok",
                            "_disease_efo_id": disease_efo_id_local,
                            **rows[0]}
                except Exception as pe:
                    logger.warning("validate_target perturbation sub-call failed: %s", pe)
                    return {"_status": "degraded_no_coverage"}

            # Run all evidence gathering concurrently — return_exceptions=True
            # ensures any sub-call failure can't crash the whole validation.
            # Each inner function already catches exceptions, but this is a
            # defense-in-depth to guarantee the tool never raises on sub-failures.
            raw_results = await asyncio.gather(
                get_omics(),
                get_completed_trials(),
                get_terminated_trials(),
                get_supporting_literature(),
                get_contradicting_literature(),
                get_chembl_activity(),
                get_perturbation(),
                return_exceptions=True,
            )

            # Unpack with safe defaults for any sub-call that raised
            def _safe(idx, default):
                v = raw_results[idx]
                return default if isinstance(v, BaseException) else v

            omics_target = _safe(0, None)
            completed_result = _safe(1, ([], 0))
            terminated_result = _safe(2, ([], 0))
            supporting_papers = _safe(3, [])
            contradicting_papers = _safe(4, [])
            chembl_activities = _safe(5, [])
            perturbation_row = _safe(6, {"_status": "degraded_no_coverage"})

            # Track which sub-calls succeeded/failed — surface in the response
            # so the UI can show "ChEMBL: unavailable" instead of silently showing 0.
            failed_sources = []
            source_names = ["omics", "completed_trials", "terminated_trials",
                            "supporting_lit", "contradicting_lit", "chembl",
                            "perturbation"]
            source_status = {}
            for i, src in enumerate(source_names):
                if isinstance(raw_results[i], BaseException):
                    failed_sources.append(src)
                    source_status[src] = "unavailable"
                    logger.warning(f"validate_target sub-call '{src}' failed: {raw_results[i]}")
                else:
                    source_status[src] = "ok"

            # Also detect silent failures: ChEMBL returning [] due to 500/timeout
            # inside the try/except of get_chembl_activity() looks like "ok" but
            # produces 0 activities indistinguishable from "target has no bioactivity."
            # Flag it so the UI can differentiate.
            if source_status.get("chembl") == "ok" and not chembl_activities:
                source_status["chembl"] = "empty_or_degraded"

            # Perturbation sub-call carries its real status as _status inside
            # the returned dict (not exception state). Reflect it in
            # source_status so the audit-trail row records the FM 13 fallback.
            if source_status.get("perturbation") == "ok":
                source_status["perturbation"] = perturbation_row.get(
                    "_status", "degraded_no_coverage"
                )

            completed_trials, n_completed_total = completed_result
            terminated_trials, n_terminated_total = terminated_result

            # === Phase 2: Score ===
            # Use total_count from ClinicalTrials.gov API (real counts, not page size)
            n_completed = n_completed_total
            n_terminated = n_terminated_total
            n_supporting = len(supporting_papers)
            n_contradicting = len(contradicting_papers)
            n_activities = len(chembl_activities)

            # Cosmos can return numeric fields as strings — coerce to float
            # to prevent format errors downstream (e.g., f"{x:.2f}" on a string).
            #
            # IMPORTANT: target_discovery's `composite_score` is now the
            # EXTENDED composite (base + perturbation contribution). The omics
            # tier here must use the unblended `base_composite_score` so the
            # perturbation signal isn't double-counted (it has its own tier
            # via perturbation_support a few lines below). Older T1-A/T1-B
            # callers without the perturbation channel fall back to
            # `composite_score` so the behavior is identical pre-Phase-0.
            try:
                if (omics_target or {}).get("base_composite_score") is not None:
                    omics_composite = float(omics_target.get("base_composite_score") or 0.0)
                else:
                    omics_composite = float((omics_target or {}).get("composite_score", 0.0) or 0.0)
            except (TypeError, ValueError):
                omics_composite = 0.0
            tractable = (omics_target or {}).get("tractability_small_molecule", False)
            try:
                known_drugs = int((omics_target or {}).get("known_drugs_count", 0) or 0)
            except (TypeError, ValueError):
                known_drugs = 0
            high_competition = known_drugs > 5

            # Mature-target classification — context-awareness layer on top of
            # the raw signal counts. For canonical oncology targets (EGFR, HER2,
            # PD-1, etc.) "high competition" and "many terminated trials" are
            # hallmarks of a validated, druggable target, not warnings. Flipping
            # those weights when maturity thresholds are met fixes the original
            # adversarial scorer's systematic under-rating of gold-standard
            # targets (reported 2026-04-21: EGFR/NSCLC scored in the "caution"
            # band despite being the textbook example of a validated target).
            #
            # Thresholds: >5 approved drugs AND >=50 completed trials. Both must
            # be present — a target with 6 approved drugs but only 8 trials is
            # narrow-indication (e.g. orphan disease) and the competition signal
            # still matters. A target with 60 trials but 0 approved drugs is
            # emerging (many in-flight programs, no validated wins yet) and the
            # termination rate still matters.
            is_mature_validated = known_drugs > 5 and n_completed >= 50
            target_maturity = (
                "mature_validated" if is_mature_validated
                else "emerging" if (known_drugs > 0 or n_completed > 5)
                else "novel"
            )

            # Best pChEMBL from activity data — coerce to float since ChEMBL
            # can return strings, and max(["7.2", "8.5"]) does string compare.
            pchembl_values = []
            for a in chembl_activities:
                v = a.get("pchembl_value")
                if v is None:
                    continue
                try:
                    pchembl_values.append(float(v))
                except (TypeError, ValueError):
                    continue
            best_pchembl = max(pchembl_values) if pchembl_values else None
            assay_types = list({a.get("standard_type") for a in chembl_activities
                               if a.get("standard_type")})

            # Tiered scoring: absolute counts with diminishing returns (log scale)
            # Each evidence stream contributes 0-1, weighted by tier
            import math

            def _evidence_strength(count, scale=5.0):
                """0 count → 0.0, scale count → ~0.63, 2*scale → ~0.86, 5*scale → ~0.99"""
                return 1.0 - math.exp(-count / scale) if count > 0 else 0.0

            # Supporting signals (weighted) — scales calibrated for real ClinicalTrials.gov counts
            clinical_base = _evidence_strength(n_completed, scale=10.0) * 3.0      # 10 trials = 0.63, 30 = 0.95
            # High-volume bonus: programs with 100+ completed trials get additional signal
            # because sample size itself validates clinical relevance (up to +0.3)
            if n_completed >= 100:
                volume_bonus = min(0.3, (n_completed - 100) / 1500.0 + 0.1)
            else:
                volume_bonus = 0.0
            clinical_support = clinical_base + volume_bonus
            chembl_support = _evidence_strength(n_activities, scale=10.0) * 2.0    # 10 assays = 0.63, 25 = 0.92
            lit_support = _evidence_strength(n_supporting, scale=5.0) * 1.0        # 5 papers = 0.63, 15 = 0.95
            omics_support = omics_composite * 1.0                                  # 0-1 directly

            # Perturbation tier (T1-C / Phase 0). Tier weight 1.5x (between
            # literature 1x and ChEMBL 2x) per locked decision. Same signed-
            # score transform as target_discovery:
            #     support = max(0, -perturbation_score) * 1.5
            # so most-reversing perturbations contribute up to +1.5 and
            # amplifying perturbations contribute 0 (never subtract).
            PERTURBATION_TIER_WEIGHT = 1.5
            pert_status = perturbation_row.get("_status", "degraded_no_coverage")
            if pert_status == "ok":
                try:
                    pert_raw_score = float(perturbation_row.get("perturbation_score") or 0.0)
                except (TypeError, ValueError):
                    pert_raw_score = 0.0
                pert_support_raw = max(0.0, -pert_raw_score)
            else:
                pert_raw_score = None
                pert_support_raw = 0.0
            perturbation_support = pert_support_raw * PERTURBATION_TIER_WEIGHT

            # Contradicting signals (weighted) — termination rate only penalizes
            # when ABOVE industry baseline. Oncology trials have ~40-50% termination
            # rates as the normal attrition floor, so below that = no penalty.
            #
            # For MATURE validated targets, terminated-trial counts are a
            # natural consequence of active pharma pipelines (EGFR has 194
            # terminated trials precisely because it's the most-studied
            # oncology target on the planet). Skip the termination penalty
            # entirely in that case — the completed/approved-drug base rate
            # already proves clinical validity.
            total_trials = n_completed + n_terminated
            if is_mature_validated:
                clinical_contra = 0.0
            elif total_trials > 0:
                termination_rate = n_terminated / total_trials
                if termination_rate < 0.40:
                    # Normal oncology attrition — no penalty
                    clinical_contra = 0.0
                elif termination_rate < 0.60:
                    # Above-average failure rate — linear penalty 0 → 1.5
                    clinical_contra = ((termination_rate - 0.40) / 0.20) * 1.5
                else:
                    # High failure rate — stronger penalty 1.5 → 3.0
                    clinical_contra = 1.5 + ((termination_rate - 0.60) / 0.40) * 1.5
            else:
                clinical_contra = 0.0
            lit_contra = _evidence_strength(n_contradicting, scale=5.0) * 1.0      # 5 papers = 0.63

            supporting_score = (
                clinical_support + chembl_support + lit_support + omics_support
                + perturbation_support
            )
            contradicting_score = clinical_contra + lit_contra

            # Max possible supporting_score:
            #   clinical (3.0) + volume bonus (0.3) + chembl (2.0)
            #   + literature (1.0) + omics (1.0) + perturbation (1.5)
            # = 8.8
            MAX_SUPPORT = 8.8
            normalized_support = min(supporting_score / MAX_SUPPORT, 1.0)

            # Contradicting evidence reduces confidence proportionally
            # Max possible contradicting = 3.0 + 1.0 = 4.0
            MAX_CONTRA = 4.0
            normalized_contra = min(contradicting_score / MAX_CONTRA, 1.0)

            # Absence penalty: if no evidence at all, score is very low
            total_evidence = n_completed + n_terminated + n_supporting + n_contradicting + n_activities
            # Sparsity tax: if no ChEMBL activity AND no omics signal, target is unvalidated
            # regardless of literature noise (Pinecone can fuzzy-match nonsense queries)
            has_real_validation = n_activities > 0 or n_completed > 0 or omics_composite > 0.3

            if total_evidence == 0 and omics_composite == 0:
                raw_confidence = 0.05  # No basis for assessment
            elif not has_real_validation:
                # Literature-only signal with no ChEMBL, no trials, no omics —
                # this is almost certainly Pinecone fuzzy-matching noise for a nonsense target
                raw_confidence = min(0.15, normalized_support * 0.3)
            elif total_evidence < 3 and omics_composite < 0.3:
                # Very sparse evidence — cap confidence low
                raw_confidence = min(0.35, normalized_support * 0.5)
            else:
                # Strong targets need strong supporting evidence AND low contradicting
                # raw_confidence = normalized_support minus a contradiction discount
                raw_confidence = normalized_support * (1.0 - 0.5 * normalized_contra)

            # Penalty adjustments
            penalties = []
            adjusted = raw_confidence
            # Competition is only a penalty for emerging / novel targets. For
            # mature validated targets, a crowded field means the target IS
            # druggable and commercially relevant — the opposite of a warning.
            # Surface it as a strength later in this function instead.
            if high_competition and not is_mature_validated:
                adjusted -= 0.05
                penalties.append(f"High competition: {known_drugs} known drugs targeting {target}")
            if not tractable and omics_target is not None:
                adjusted -= 0.15
                penalties.append(f"{target} not classified as small-molecule tractable")

            # Phase III failures — strongest negative signal, but scaled by maturity.
            # For emerging/novel targets, each Phase III failure is -0.20 (a single
            # Phase III failure is genuinely alarming for a target with 10 total trials).
            # For mature targets (200+ completed trials), individual Phase III failures
            # are expected — programs fail for commercial, patient-selection, or dosing
            # reasons, not because the target is invalid. Cap the total Phase III
            # penalty at -0.20 for these targets (equivalent to one failure on a
            # novel target). EGFR has 553 completed trials; 5 Phase III failures in
            # that context is a 0.9% failure rate, not a warning signal.
            phase3_failures = [t for t in terminated_trials
                               if t.get("phase") and "PHASE3" in t.get("phase", "")]
            if phase3_failures:
                if is_mature_validated or n_completed >= 200:
                    # Mature target: cap Phase III penalty at -0.20 total
                    adjusted -= min(0.20, len(phase3_failures) * 0.05)
                else:
                    # Novel/emerging target: each Phase III failure is -0.20
                    for _ in phase3_failures:
                        adjusted -= 0.20
                penalties.append(
                    f"{len(phase3_failures)} Phase III failure(s) — strongest negative clinical signal"
                )

            confidence = max(0.0, min(1.0, adjusted))

            # Clinical-evidence floor: targets with massive trial evidence can't
            # score "Reconsider." 200+ completed clinical trials = clinical
            # significance established at a population level. This prevents
            # transient ChEMBL timeouts + Phase III pagination noise from tanking
            # the score of gold-standard targets like EGFR (553 completed trials,
            # 3 FDA-approved drugs). Floor at 0.50 (solidly in "proceed_with_caution").
            if n_completed >= 200:
                confidence = max(confidence, 0.50)

            # Classification — thresholds calibrated to real drug discovery confidence
            # High ≥0.70: strong evidence, proceed with compute
            # Medium ≥0.45: some evidence, proceed with caution
            # Low <0.45: sparse/contradicted evidence, reconsider
            if confidence >= 0.70:
                confidence_level = "high"
                recommendation = "proceed"
            elif confidence >= 0.45:
                confidence_level = "medium"
                recommendation = "proceed_with_caution"
            else:
                confidence_level = "low"
                recommendation = "reconsider"

            # === Phase 3: Build report ===
            risk_factors = list(penalties)
            # Terminated trials is a risk signal for novel / emerging targets
            # but an inevitable artifact of volume for mature ones. A target
            # with 640 completed + 194 terminated trials (EGFR/NSCLC) isn't
            # "risky because 194 terminated" — it's "intensely studied and
            # the field is figuring out indications + compound chemistry".
            if n_terminated > 0 and not is_mature_validated:
                risk_factors.append(
                    f"{n_terminated} terminated trial(s) for {target} in {disease}"
                )
            if n_contradicting > 2:
                risk_factors.append(
                    f"{n_contradicting} papers with failure/resistance/toxicity signals"
                )

            strengths = []
            # Lead with the maturity narrative when it applies — this is what
            # turns a confusing "high competition → penalty" into the actual
            # signal: the target has already been de-risked clinically.
            if is_mature_validated:
                strengths.append(
                    f"Mature validated target: {known_drugs} approved drug(s) and "
                    f"{n_completed} completed trial(s) — clinical druggability proven. "
                    f"High activity in this space is a signal of commercial viability, not crowding."
                )
            if omics_composite > 0.7:
                strengths.append(
                    f"Strong omics signal (composite: {omics_composite:.2f})"
                )
            if n_completed > 3 and not is_mature_validated:
                # For mature targets, completed-trial count is already captured
                # in the maturity strength above — don't repeat it.
                strengths.append(
                    f"{n_completed} completed clinical trials demonstrate clinical precedent"
                )
            if n_activities > 5:
                pchembl_note = f", best pChEMBL: {best_pchembl:.1f}" if best_pchembl else ""
                strengths.append(
                    f"{n_activities} ChEMBL assays confirm druggability{pchembl_note}"
                )
            if tractable:
                strengths.append("Classified as small-molecule tractable")
            if n_supporting > 5:
                strengths.append(
                    f"{n_supporting} supporting publications in literature"
                )

            # Top evidence items (limit to 3 each for readability)
            top_failures = [
                {"nct_id": t.get("nct_id"), "title": t.get("title", "")[:100],
                 "phase": t.get("phase"), "status": t.get("status")}
                for t in terminated_trials[:3]
            ]
            top_successes = [
                {"nct_id": t.get("nct_id"), "title": t.get("title", "")[:100],
                 "phase": t.get("phase"), "status": t.get("status")}
                for t in completed_trials[:3]
            ]
            top_supporting_lit = [
                {"title": p.get("title", "")[:100], "year": p.get("year"),
                 "score": round(p.get("score", 0), 3)}
                for p in supporting_papers[:3]
            ]
            top_contradicting_lit = [
                {"title": p.get("title", "")[:100], "year": p.get("year"),
                 "score": round(p.get("score", 0), 3)}
                for p in contradicting_papers[:3]
            ]

            result = ToolResult(
                success=True,
                data={
                    "target": target.upper(),
                    "disease": disease,
                    "confidence_score": round(confidence, 3),
                    "confidence_level": confidence_level,
                    "recommendation": recommendation,
                    "target_maturity": target_maturity,  # "mature_validated" | "emerging" | "novel"
                    "evidence": {
                        "omics": {
                            "composite_score": omics_composite,
                            "genetic_score": (omics_target or {}).get("genetic_score"),
                            "expression_score": (omics_target or {}).get("expression_score"),
                            "tractable": tractable,
                            "known_drugs": known_drugs,
                            "high_competition": high_competition,
                            "suggested_pdb_id": (omics_target or {}).get("suggested_pdb_id"),
                        },
                        "clinical_trials": {
                            "completed": n_completed,
                            "terminated": n_terminated,
                            "phase3_failures": len(phase3_failures),
                            "key_failures": top_failures,
                            "key_successes": top_successes,
                        },
                        "literature": {
                            "supporting_papers": n_supporting,
                            "contradicting_papers": n_contradicting,
                            "top_supporting": top_supporting_lit,
                            "top_contradicting": top_contradicting_lit,
                            "min_score_threshold": LIT_MIN_SCORE,
                        },
                        "chembl": {
                            "activity_count": n_activities,
                            "best_pchembl": best_pchembl,
                            "assay_types": assay_types[:5],
                        },
                        "perturbation": {
                            "status": pert_status,  # "ok" | "degraded_no_coverage" | "disabled"
                            "score": pert_raw_score,
                            "dataset": perturbation_row.get("dataset_source"),
                            "signature_version": perturbation_row.get("signature_version"),
                            "perturbation_data_version": perturbation_row.get("perturbation_data_version"),
                            "license_tag": perturbation_row.get("license_tag"),
                            "n_overlap_up": perturbation_row.get("n_overlap_up"),
                            "n_overlap_down": perturbation_row.get("n_overlap_down"),
                            "tier_weight": PERTURBATION_TIER_WEIGHT,
                            "support_contribution": perturbation_support,
                        },
                    },
                    "risk_factors": risk_factors,
                    "strengths": strengths,
                    "partial_data": failed_sources if failed_sources else None,
                    "source_status": source_status,
                    "_audit_reminder": (
                        "MANDATORY: Present this validation report as a STOP checkpoint. "
                        "Wait for the user's decision, then call save_funnel_stage with "
                        "stage_name='target_validation', human_reviewed=true, "
                        "and include confidence_score + recommendation in results_summary."
                    ),
                    "tool_suggestions": [
                        self._tool_suggestion("save_funnel_stage", "Log this validation checkpoint (MANDATORY in funnel mode)"),
                        self._tool_suggestion("search_literature", f"Deep dive into {target} {disease} literature"),
                        self._tool_suggestion("dock_molecules", f"Dock known actives against {target}"),
                    ]
                },
                usage={"queries": 6, "tool": "validate_target"}
            )

            # Cache the result for 1 hour so repeated calls produce identical data.
            # This eliminates the "EGFR scores 0.54 on Claude but 0.04 on NovoWorkbench"
            # variance from non-deterministic upstream API responses.
            if r:
                try:
                    await r.set(cache_key, json.dumps(result.data), ex=3600)
                except Exception:
                    pass  # Non-fatal — cache write failure doesn't affect the response

            return result

        except Exception as e:
            logger.exception(f"Error in validate_target: {e}")
            return ToolResult(success=False, error=f"Target validation failed: {str(e)}")

    async def _execute_stratify_patients(self, args: Dict[str, Any]) -> ToolResult:
        """Assess clinical viability via pharmacogenomics and resistance analysis.

        Validates the target gene symbol against HGNC before any lookup work happens.
        Unknown symbols return a structured error (not a silent empty response).
        """
        smiles = args.get("smiles")
        target_gene = args.get("target_gene") or args.get("gene_symbol")
        if not smiles or not target_gene:
            missing = [name for name, val in (("smiles", smiles), ("target_gene", target_gene)) if not val]
            return ToolResult(
                success=False,
                error=f"Missing required parameter{'s' if len(missing) > 1 else ''}: {', '.join(missing)}",
            )

        # === Gene symbol validation (fail loudly on unknown input) ===
        from core.validators import validate_gene_symbol
        gene_validation = await validate_gene_symbol(target_gene)

        if not gene_validation.valid:
            # Alias/previous symbol — return retry hint
            if gene_validation.suggested_symbol:
                return ToolResult(
                    success=False,
                    error=gene_validation.message,
                    data={
                        "status": "error",
                        "error_code": "GENE_SYMBOL_ALIAS",
                        "gene": gene_validation.symbol,
                        "suggested_symbol": gene_validation.suggested_symbol,
                        "message": gene_validation.message,
                        "retry_with": {"target_gene": gene_validation.suggested_symbol},
                    },
                )
            # Truly unknown gene — return actionable error (NOT a silent empty success)
            return ToolResult(
                success=False,
                error=gene_validation.message or f"Gene '{target_gene}' not recognized",
                data={
                    "status": "error",
                    "error_code": "GENE_NOT_RECOGNIZED",
                    "gene": gene_validation.symbol,
                    "message": gene_validation.message,
                    "hgnc_search_url": f"https://www.genenames.org/tools/search/#!/?query={gene_validation.symbol}",
                },
            )

        # Gene is valid — use the normalized symbol for downstream lookups
        target_gene = gene_validation.symbol

        # Case B: Valid HGNC gene but NOT in our PGx panel.
        # The tool's purpose is PGx + resistance analysis. Running CYP substrate logic
        # against non-pharmacogenes produces misleading output. Return early with a
        # clear "not_applicable" verdict and point the caller at resistance-only data.
        if not gene_validation.has_pgx_data:
            return ToolResult(
                success=True,
                data={
                    "status": "not_applicable",
                    "target_gene": target_gene,
                    "hgnc_id": gene_validation.hgnc_id,
                    "gene_name": gene_validation.gene_name,
                    "validation": {
                        "valid_hgnc_symbol": True,
                        "has_pgx_data": False,
                        "source": gene_validation.source,
                    },
                    "summary": {
                        "clinical_viability": "not_applicable",
                        "key_risks": [
                            f"{target_gene} is a valid HGNC-approved gene but is not in the "
                            f"NovoMCP pharmacogene panel (56 CYP/UGT/SLC/HLA genes). "
                            f"PGx-based patient stratification does not apply."
                        ],
                        "recommended_actions": [
                            f"For resistance profiling of {target_gene}, query omics_resistance "
                            f"directly via target_discovery.",
                            "For PGx stratification, select a target that is metabolized by a "
                            "CYP/UGT pharmacogene (see the 56-gene panel in core/validators.py).",
                        ],
                    },
                    "tool_suggestions": [
                        self._tool_suggestion("target_discovery", f"Check omics signal and resistance variants for {target_gene}"),
                        self._tool_suggestion("validate_target", f"Adversarially validate {target_gene} before committing compute"),
                    ],
                },
                usage={"queries": 1, "tool": "stratify_patients"}
            )

        indication = args.get("indication", "")
        include_pgx = args.get("include_pgx", True)
        include_biomarkers = args.get("include_biomarkers", True)
        admet_results = args.get("admet_results", {})

        try:
            # Omics data lives in Aurora (omics schema). All lookups go through
            # core.db_helper.query_sql; no per-call client setup needed here.
            result_data = {
                "candidate_smiles": smiles,
                "target_gene": target_gene,
                "indication": indication,
            }

            # ---- Step A: CYP substrate identification ----
            # Extract CYP substrate probabilities from ADMET results
            # Handles multiple key formats from different ADMET sources
            def _extract_cyp_prob(admet: dict, cyp_name: str):
                """Check multiple key formats for CYP substrate probability."""
                candidates = [
                    f"{cyp_name.lower()}_substrate_probability",
                    f"{cyp_name.lower()}_substrate",
                    f"{cyp_name.upper()}_Substrate",
                    f"{cyp_name}_substrate_probability",
                    f"{cyp_name}_substrate",
                ]
                # Check top-level keys
                for key in candidates:
                    val = admet.get(key)
                    if val is not None:
                        if isinstance(val, str):
                            return 1.0 if val.lower() in ("yes", "true", "1", "substrate") else 0.0
                        return float(val)
                # Check nested under metabolism category
                metabolism = admet.get("metabolism", {})
                if isinstance(metabolism, dict):
                    for key in candidates:
                        val = metabolism.get(key)
                        if val is not None:
                            if isinstance(val, str):
                                return 1.0 if val.lower() in ("yes", "true", "1", "substrate") else 0.0
                            return float(val)
                return None

            cyp_substrates = {}
            for cyp in ["CYP3A4", "CYP2D6", "CYP2C9"]:
                prob = _extract_cyp_prob(admet_results, cyp)
                if prob is not None and prob > 0.5:
                    cyp_substrates[cyp] = prob

            primary_metabolism = list(cyp_substrates.keys()) if cyp_substrates else ["Unknown — pass admet_results from Step 4"]

            # ---- Step B: PGx lookup ----
            pharmacogenomics = {"primary_metabolism": primary_metabolism}
            pgx_risk_alleles = []
            population_coverage = {}

            if include_pgx and cyp_substrates:
                from core.db_helper import query_sql
                ancestry_weights = {
                    "european": 0.16, "african": 0.17, "latino": 0.14,
                    "south_asian": 0.25, "east_asian": 0.28,
                }

                total_normal_pct = 0
                total_weight = 0
                coverage_by_ancestry = []

                for cyp, prob in cyp_substrates.items():
                    try:
                        rows = await query_sql(
                            """
                            SELECT gene_symbol, cpic_level, gene_function,
                                   clinical_implications, key_alleles,
                                   metabolizer_phenotypes, population_frequencies,
                                   variant_count_gnomad
                              FROM omics.omics_pgx
                             WHERE gene_symbol = %s
                            """,
                            (cyp,),
                        )
                        if not rows:
                            continue
                        doc = rows[0]
                        pharmacogenomics[f"{cyp}_cpic_level"] = doc.get("cpic_level", "")
                        pharmacogenomics[f"{cyp}_substrate_probability"] = prob
                        pharmacogenomics[f"{cyp}_clinical_implications"] = doc.get("clinical_implications", "")

                        # Collect risk alleles
                        for allele in (doc.get("key_alleles") or [])[:5]:
                            if allele.get("function") in ("no function", "decreased function"):
                                pgx_risk_alleles.append({
                                    "gene": cyp,
                                    "allele": allele.get("allele", ""),
                                    "effect": allele.get("function", ""),
                                })

                        # Step C: Population coverage
                        pop_freqs = doc.get("population_frequencies") or {}
                        for ancestry, weight in ancestry_weights.items():
                            freqs = pop_freqs.get(ancestry, {})
                            nm_pct = freqs.get("nm_pct", 80)
                            total_normal_pct += nm_pct * weight
                            total_weight += weight

                            coverage_by_ancestry.append({
                                "ancestry": ancestry,
                                "cyp": cyp,
                                "normal_metabolizer_pct": nm_pct,
                            })

                    except Exception as e:
                        logger.warning(f"PGx lookup failed for {cyp}: {e}")

                if total_weight > 0:
                    global_coverage = round(total_normal_pct / total_weight, 1)
                else:
                    global_coverage = 0

                population_coverage = {
                    "global_normal_metabolizer_pct": global_coverage,
                    "by_ancestry": coverage_by_ancestry,
                }

            pharmacogenomics["pgx_risk_alleles"] = pgx_risk_alleles
            result_data["pharmacogenomics"] = pharmacogenomics
            result_data["population_coverage"] = population_coverage

            # ---- Step D: Resistance mutation lookup ----
            resistance = {"known_mutations": [], "resistance_risk": "unknown"}

            if include_biomarkers:
                try:
                    from core.db_helper import query_sql
                    # Order by binding-site flag first so the 50-row cap doesn't
                    # truncate the most clinically relevant variants. The
                    # ix_omics_resistance_binding partial index covers the
                    # TRUE branch; the rest fall back to ix_omics_resistance_gene.
                    mutations = await query_sql(
                        """
                        SELECT variant, cancer_type, clinvar_significance,
                               affects_binding_site
                          FROM omics.omics_resistance
                         WHERE gene_symbol = %s
                         ORDER BY affects_binding_site DESC NULLS LAST
                         LIMIT 50
                        """,
                        (target_gene,),
                    )

                    known_mutations = []
                    for mut in mutations:
                        known_mutations.append({
                            "mutation": mut.get("variant", ""),
                            "cancer_type": mut.get("cancer_type", ""),
                            "clinvar_significance": mut.get("clinvar_significance", ""),
                            "affects_binding_site": mut.get("affects_binding_site", False),
                        })

                    # Assess resistance risk
                    n_pathogenic = len(known_mutations)
                    n_binding_site = sum(1 for m in known_mutations if m["affects_binding_site"])
                    if n_binding_site > 3:
                        risk = "high"
                    elif n_pathogenic > 20:
                        risk = "moderate"
                    elif n_pathogenic > 0:
                        risk = "low"
                    else:
                        risk = "minimal"

                    resistance = {
                        "known_mutations": known_mutations,
                        "total_pathogenic_variants": n_pathogenic,
                        "variants_near_binding_site": n_binding_site,
                        "resistance_risk": risk,
                    }

                except Exception as e:
                    logger.warning(f"Resistance lookup failed for {target_gene}: {e}")
                    resistance["error"] = str(e)

            result_data["resistance"] = resistance

            # ---- Step E: Clinical viability summary ----
            viability = "moderate"
            key_risks = []
            recommended_actions = []

            global_cov = population_coverage.get("global_normal_metabolizer_pct", 0)
            if global_cov > 70:
                viability = "high"
            elif global_cov < 50:
                viability = "low"
                key_risks.append(f"Low population coverage ({global_cov}% normal metabolizers)")

            if resistance["resistance_risk"] in ("high", "moderate"):
                if viability == "high":
                    viability = "moderate"
                key_risks.append(f"{resistance.get('total_pathogenic_variants', 0)} known resistance variants in {target_gene}")

            if not cyp_substrates:
                key_risks.append("CYP substrate data not provided — pass admet_results from Stage 5 for full PGx analysis")
                recommended_actions.append("Re-run with admet_results parameter including CYP substrate probabilities")

            if pgx_risk_alleles:
                recommended_actions.append(f"Consider dose adjustment guidance for {', '.join(set(a['gene'] for a in pgx_risk_alleles))}")

            if resistance.get("total_pathogenic_variants", 0) > 0:
                recommended_actions.append(f"Evaluate resistance profile — {resistance['total_pathogenic_variants']} known pathogenic variants in {target_gene}")

            result_data["summary"] = {
                "clinical_viability": viability,
                "key_risks": key_risks,
                "recommended_actions": recommended_actions,
            }

            return ToolResult(
                success=True,
                data=result_data,
                usage={"queries": 3, "tool": "stratify_patients"}
            )

        except Exception as e:
            logger.exception(f"Error in stratify_patients: {e}")
            return ToolResult(success=False, error=f"Patient stratification failed: {str(e)}")

    # =========================================================================
    # Pipeline Compute Tool Executors (lead_optimization, dock_molecules, run_molecular_dynamics)
    # =========================================================================

    def _compute_scaffold_diversity(
        self, variants: List[Dict[str, Any]], butina_threshold: float = 0.4
    ) -> Dict[str, Any]:
        """Cluster variants by structural similarity + Murcko scaffold identity.

        Annotates each variant in place with:
          - murcko_scaffold (SMILES of Bemis-Murcko scaffold)
          - murcko_cluster_id (int, groupby exact murcko_scaffold match)
          - scaffold_cluster_id (int, Butina clustering on Morgan FP Tanimoto)
          - cluster_size (int, how many variants share this Butina cluster)
          - cluster_note (str | None, human-readable diversity warning)

        Returns summary dict with:
          - unique_scaffolds (int)
          - diversity_score (float 0-1, unique_scaffolds / n_variants)
          - n_clusters (int, Butina cluster count)
          - clusters (list of {cluster_id, size, members: [smiles]})
        """
        if not variants:
            return {
                "unique_scaffolds": 0,
                "diversity_score": 1.0,
                "n_clusters": 0,
                "clusters": [],
            }

        try:
            from rdkit import Chem, DataStructs
            from rdkit.Chem import AllChem
            from rdkit.Chem.Scaffolds import MurckoScaffold
            from rdkit.ML.Cluster import Butina
        except ImportError:
            logger.warning("RDKit not available — skipping scaffold diversity")
            return {
                "unique_scaffolds": len(variants),
                "diversity_score": 1.0,
                "n_clusters": len(variants),
                "clusters": [],
            }

        # --- Murcko scaffolds (exact-match clustering) ---
        murcko_map: Dict[str, int] = {}
        next_murcko_id = 0
        for v in variants:
            smi = v.get("smiles", "")
            scaffold = ""
            try:
                mol = Chem.MolFromSmiles(smi)
                if mol is not None:
                    scaffold_mol = MurckoScaffold.GetScaffoldForMol(mol)
                    scaffold = Chem.MolToSmiles(scaffold_mol) if scaffold_mol else ""
            except Exception:
                pass
            v["murcko_scaffold"] = scaffold
            if scaffold and scaffold not in murcko_map:
                murcko_map[scaffold] = next_murcko_id
                next_murcko_id += 1
            v["murcko_cluster_id"] = murcko_map.get(scaffold, -1)

        unique_scaffolds = len({s for s in murcko_map.keys() if s})

        # --- Butina clustering on Morgan FP Tanimoto ---
        fps = []
        valid_indices = []
        for i, v in enumerate(variants):
            try:
                mol = Chem.MolFromSmiles(v.get("smiles", ""))
                if mol is not None:
                    fp = AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=2048)
                    fps.append(fp)
                    valid_indices.append(i)
            except Exception:
                pass

        n_clusters = 0
        clusters_list: List[Dict[str, Any]] = []
        if len(fps) >= 2:
            # Lower triangular distance matrix (1 - Tc), flattened for Butina.
            # rdkit's ClusterData expects distances [dist(1,0), dist(2,0), dist(2,1), ...].
            dists: List[float] = []
            for i in range(1, len(fps)):
                sims = DataStructs.BulkTanimotoSimilarity(fps[i], fps[:i])
                dists.extend([1.0 - s for s in sims])

            try:
                clusters = Butina.ClusterData(
                    dists, len(fps), butina_threshold, isDistData=True, reordering=True
                )
            except Exception as e:
                logger.warning(f"Butina clustering failed: {e}")
                clusters = [(i,) for i in range(len(fps))]

            # Assign cluster IDs back to variants
            for cluster_idx, cluster_members in enumerate(clusters):
                for member in cluster_members:
                    variant_idx = valid_indices[member]
                    variants[variant_idx]["scaffold_cluster_id"] = cluster_idx
                    variants[variant_idx]["cluster_size"] = len(cluster_members)

                clusters_list.append({
                    "cluster_id": cluster_idx,
                    "size": len(cluster_members),
                    "members": [variants[valid_indices[m]].get("smiles", "") for m in cluster_members],
                })
            n_clusters = len(clusters)
        else:
            # Trivial case: 0 or 1 variant — each is its own cluster
            for i, v in enumerate(variants):
                v["scaffold_cluster_id"] = i
                v["cluster_size"] = 1
            n_clusters = len(variants)

        # Human-readable cluster_note for variants in multi-member clusters
        for v in variants:
            size = v.get("cluster_size", 1)
            if size > 1:
                v["cluster_note"] = f"{size - 1} other variant(s) share this scaffold cluster"
            else:
                v["cluster_note"] = None
            # Default murcko_cluster_id if missing
            if "murcko_cluster_id" not in v:
                v["murcko_cluster_id"] = -1

        diversity_score = unique_scaffolds / len(variants) if variants else 1.0

        return {
            "unique_scaffolds": unique_scaffolds,
            "diversity_score": round(diversity_score, 3),
            "n_clusters": n_clusters,
            "clusters": clusters_list,
        }

    def _rank_by_diversity(self, variants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Diversity-aware ranking: pick the best (highest QED) variant from each
        Butina cluster first, then fill remaining slots with next-best from
        already-represented clusters.

        Adds `diversity_rank` (1-based) to each variant. The returned list is
        sorted by diversity_rank ascending (distinct scaffolds first).

        The original QED sort is preserved in the response since diversity_rank
        is additive — clients can sort by either.
        """
        if not variants:
            return variants

        # Group by scaffold_cluster_id, sort each cluster by QED desc
        from collections import defaultdict
        clusters: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for v in variants:
            cid = v.get("scaffold_cluster_id", 0)
            clusters[cid].append(v)
        for cid in clusters:
            clusters[cid].sort(key=lambda v: v.get("qed") or 0, reverse=True)

        # Interleave: take best-per-cluster in round-robin order by global QED rank
        sorted_cluster_ids = sorted(
            clusters.keys(),
            key=lambda c: clusters[c][0].get("qed") or 0,
            reverse=True,
        )
        ranked: List[Dict[str, Any]] = []
        cursor = {cid: 0 for cid in sorted_cluster_ids}
        while any(cursor[cid] < len(clusters[cid]) for cid in sorted_cluster_ids):
            for cid in sorted_cluster_ids:
                if cursor[cid] < len(clusters[cid]):
                    ranked.append(clusters[cid][cursor[cid]])
                    cursor[cid] += 1

        for i, v in enumerate(ranked, 1):
            v["diversity_rank"] = i

        return ranked

    async def _execute_lead_optimization(self, args: Dict[str, Any]) -> ToolResult:
        """Generate optimized molecular variants via scaffold hopping."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        optimization_type = args.get("optimization_type", "scaffold_hop")
        # Accept both num_variants (TS schema) and max_variants (legacy) for compatibility
        max_variants = min(args.get("num_variants") or args.get("max_variants", 10), 50)
        target_properties = args.get("target_properties", {})

        # Configurable Tanimoto similarity range (Theo P1)
        # Default broad range (0.3-0.85) doesn't filter anything by default while
        # still excluding near-identical matches. Theo's recommended tighter
        # ranges: 0.80-0.85 for SAR predictability around a lead; 0.75-0.85 for
        # a patent-safe family.
        sim_range = args.get("similarity_range") or {}
        sim_min = float(sim_range.get("min", 0.3))
        sim_max = float(sim_range.get("max", 0.85))
        if sim_min < 0 or sim_max > 1 or sim_min >= sim_max:
            return ToolResult(
                success=False,
                error=f"Invalid similarity_range: min={sim_min}, max={sim_max}. "
                      f"Must satisfy 0 ≤ min < max ≤ 1."
            )

        # Configurable patent_risk thresholds (Theo P1)
        # Default breakpoints: Tc >= 0.7 = high, 0.4-0.7 = low, < 0.4 = novel
        pr_thresholds = args.get("patent_risk_thresholds") or {}
        pr_low = float(pr_thresholds.get("low", 0.4))
        pr_high = float(pr_thresholds.get("high", 0.7))
        if pr_low < 0 or pr_high > 1 or pr_low >= pr_high:
            return ToolResult(
                success=False,
                error=f"Invalid patent_risk_thresholds: low={pr_low}, high={pr_high}. "
                      f"Must satisfy 0 ≤ low < high ≤ 1."
            )

        try:
            # Submit to lead-optimization service
            response = await self._call_service(
                "lead-optimization",
                "/optimize",
                {
                    "smiles": smiles,
                    "optimization_type": optimization_type,
                    "n_candidates": max_variants,
                    "target_properties": target_properties,
                },
                timeout=180.0,
            )

            if response.status_code != 200:
                return ToolResult(success=False, error=f"Lead optimization service error: {response.status_code}")

            data = response.json()
            variants = data.get("variants", data.get("candidates", []))

            if not variants:
                # No variants generated — do not charge credits for null results
                # Surface the service's diagnostic if available
                diagnostic = data.get("diagnostic", (
                    "No variants generated. The input SMILES may be too complex "
                    "(highly fused polycyclic systems, unusual heteroatom patterns) "
                    "for the scaffold hopping generator. Try a more drug-like seed "
                    "or use optimize_molecule (MolMIM) for property-directed edits."
                ))
                return ToolResult(
                    success=True,
                    data={
                        "input_smiles": smiles,
                        "variants": [],
                        "message": diagnostic,
                        "credits_refunded": True,
                    },
                    usage={"queries": 0, "tool": "lead_optimization", "_dynamic_credits": 0}
                )

            # Collect variant SMILES for batch Tanimoto via faves-compliance
            variant_smiles_list = [
                v.get("smiles", "") for v in variants[:max_variants] if v.get("smiles")
            ]

            # Batch pairwise Tanimoto: seed vs all variants (single call)
            patent_risk_map = {}  # smiles -> {tanimoto, patent_risk, note}
            if variant_smiles_list:
                try:
                    tc_resp = await self._call_service(
                        "faves-compliance", "/api/similarity/pairwise",
                        {"smiles_a": smiles, "smiles_b": variant_smiles_list},
                        timeout=30.0
                    )
                    if tc_resp.status_code == 200:
                        for comp in tc_resp.json().get("comparisons", []):
                            patent_risk_map[comp["smiles"]] = {
                                "tanimoto_to_seed": comp.get("tanimoto"),
                                "patent_risk": comp.get("patent_risk"),
                                "patent_note": comp.get("note"),
                            }
                except Exception:
                    pass

            # Enrich variants and filter invalid SMILES
            enriched_variants = []
            fragmented_count = 0
            for variant in variants[:max_variants]:
                v_smiles = variant.get("smiles", "")
                if not v_smiles:
                    continue

                # Filter disconnected SMILES (`.` separator = salt/fragment mixtures)
                # These are not valid single-molecule drug candidates
                if "." in v_smiles:
                    fragmented_count += 1
                    continue

                enriched = {
                    "smiles": v_smiles,
                    "modification": variant.get("modification", ""),
                    "mw": variant.get("mw"),
                    "logp": variant.get("logp"),
                    "qed": variant.get("qed"),
                    "tpsa": variant.get("tpsa"),
                }

                # Apply patent risk from batch Tanimoto
                # Try both raw and canonical SMILES keys (services may canonicalize)
                pr = patent_risk_map.get(v_smiles)
                if not pr:
                    try:
                        from rdkit import Chem
                        canon = Chem.MolToSmiles(Chem.MolFromSmiles(v_smiles))
                        pr = patent_risk_map.get(canon)
                    except Exception:
                        pass
                if pr:
                    enriched.update(pr)

                # Single FAVES call handles both compliance AND properties
                # (faves-compliance calculates properties internally via chem-props)
                try:
                    faves = await self._faves_context_free(v_smiles)
                    compliance = faves.get("compliance", {})
                    is_flagged = compliance.get("status") in ["controlled", "flagged"]

                    if is_flagged:
                        enriched["compliance_status"] = "flagged"
                        continue  # Skip controlled/flagged variants
                    enriched["compliance_status"] = "clean"

                    # Extract properties from the same /api/classify response
                    raw = faves.get("raw", {})
                    tox = raw.get("toxicity_summary", {})
                    # SA via local RDKit sascorer (chem-props returns flat 1.0).
                    enriched["sa_score"] = _compute_sa_score(v_smiles) or raw.get("synthetic_accessibility") or enriched.get("sa_score")
                    enriched["hbd"] = tox.get("hbd")
                    enriched["hba"] = tox.get("hba")
                    rot_bonds = tox.get("rotatable_bonds") or 0
                    tpsa_val = tox.get("tpsa") or 0
                    enriched["rotatable_bonds"] = rot_bonds
                    enriched["lipinski_violations"] = tox.get("lipinski_violations")
                    # Compute veber_violations inline (faves doesn't return it)
                    enriched["veber_violations"] = (1 if rot_bonds > 10 else 0) + (1 if tpsa_val > 140 else 0)
                    # Backfill MW/LogP/TPSA/QED from FAVES if the service didn't return them
                    if not enriched.get("mw"):
                        enriched["mw"] = tox.get("molecular_weight")
                    if not enriched.get("logp"):
                        enriched["logp"] = tox.get("logp")
                    if not enriched.get("tpsa"):
                        enriched["tpsa"] = tpsa_val
                    if not enriched.get("qed"):
                        enriched["qed"] = raw.get("qed")

                    # Prior art / disclosure checking (Theo P2)
                    # Surface InChIKey-based identity resolution: is this variant already
                    # disclosed in PubChem or the 122M local DB? If yes, composition-of-matter
                    # patenting may be forfeited regardless of Tanimoto distance to seed.
                    # Always emit prior_art with a disclosed sentinel so the viewer can
                    # distinguish "lookup didn't run" (null) from "novel" (false).
                    pa = raw.get("prior_art") if isinstance(raw, dict) else None
                    # Only trust a POSITIVE disclosure (disclosed=True → found it).
                    # While the SMILES index is only ~22% backfilled (~53M/244M,
                    # 2026-06-07), a disclosed=False is unreliable (the molecule may
                    # be un-indexed), so downgrade it to unknown (None) to avoid a
                    # false "novel" patent-clearance signal. Restore the False path
                    # once the backfill completes.
                    if isinstance(pa, dict) and pa.get("disclosed") is True:
                        enriched["prior_art"] = {
                            "disclosed": True,
                            "pubchem_cid": pa.get("pubchem_cid"),
                            "disclosure_source": pa.get("disclosure_source"),
                            "inchikey": pa.get("inchikey"),
                        }
                    else:
                        enriched["prior_art"] = {
                            "disclosed": None,
                            "pubchem_cid": None,
                            "disclosure_source": "index_incomplete" if isinstance(pa, dict) else None,
                            "inchikey": None,
                        }
                except Exception:
                    enriched["compliance_status"] = "unchecked"

                enriched_variants.append(enriched)

            # --- Configurable Tanimoto filtering + patent_risk reclassification (Theo P1) ---
            # Apply user-provided similarity_range and patent_risk_thresholds.
            # Filter OUT variants with tanimoto_to_seed outside [sim_min, sim_max].
            # Reclassify patent_risk using configured breakpoints.
            filtered_by_similarity = 0
            similarity_filtered_variants: List[Dict[str, Any]] = []
            using_custom_sim_range = not (sim_min == 0.3 and sim_max == 0.85)
            using_custom_pr_thresholds = not (pr_low == 0.4 and pr_high == 0.7)

            for v in enriched_variants:
                tc = v.get("tanimoto_to_seed")
                # Reclassify patent_risk if custom thresholds provided and Tc known
                if using_custom_pr_thresholds and isinstance(tc, (int, float)):
                    if tc >= pr_high:
                        v["patent_risk"] = "high"
                    elif tc >= pr_low:
                        v["patent_risk"] = "low"
                    else:
                        v["patent_risk"] = "novel"

                # Filter by similarity_range (only when Tc is known — don't drop
                # variants that didn't get a Tc computed, that's a separate failure mode)
                if isinstance(tc, (int, float)) and (tc < sim_min or tc > sim_max):
                    filtered_by_similarity += 1
                    continue
                similarity_filtered_variants.append(v)

            enriched_variants = similarity_filtered_variants

            # --- Scaffold diversity analysis (Theo P0) ---------------------------
            # Guards against QSAR homogeneity: "You will think you have 10 great
            # leads, when in fact you've only found ten versions of the same
            # molecule." (Theo, Apr 8)
            #
            # Two clustering signals:
            #   1. Butina clustering on Morgan fingerprint Tanimoto distances
            #      (handles soft similarity — e.g. methyl vs ethyl substitutions
            #      on the same ring)
            #   2. Murcko scaffold exact-match (handles hard structural identity —
            #      e.g. 10 variants that all reduce to the same core scaffold)
            diversity_info = self._compute_scaffold_diversity(enriched_variants)

            # Diversity-aware ranking: pick the best variant (by QED) from each
            # cluster first, then fill remaining slots. This surfaces structurally
            # distinct leads at the top instead of near-duplicates.
            enriched_variants = self._rank_by_diversity(enriched_variants)

            result_data = {
                    "input_smiles": smiles,
                    "optimization_type": optimization_type,
                    "num_variants": len(enriched_variants),
                    "variants": enriched_variants,
                    # Scaffold diversity summary (P0)
                    "unique_scaffolds": diversity_info["unique_scaffolds"],
                    "diversity_score": diversity_info["diversity_score"],
                    "n_clusters": diversity_info["n_clusters"],
                    "clusters": diversity_info["clusters"],
                    # Configurable Tanimoto filtering (Theo P1)
                    "similarity_range": {"min": sim_min, "max": sim_max},
                    "patent_risk_thresholds": {"low": pr_low, "high": pr_high},
                    "filtered_by_similarity": filtered_by_similarity,
                    "tool_suggestions": [
                        self._tool_suggestion("dock_molecules", "Dock top variants against target protein"),
                        self._tool_suggestion("predict_admet", "Run detailed ADMET on top candidates"),
                    ],
            }
            if filtered_by_similarity > 0:
                result_data["similarity_filter_note"] = (
                    f"{filtered_by_similarity} variant(s) filtered because Tanimoto to seed "
                    f"fell outside [{sim_min}, {sim_max}]. Adjust similarity_range to widen."
                )
            if fragmented_count > 0:
                result_data["fragmented_filtered"] = fragmented_count
                result_data["fragmented_note"] = (
                    f"{fragmented_count} variant(s) contained disconnected SMILES "
                    f"(salt/fragment mixtures) and were excluded. This is a known "
                    f"limitation of the scaffold hopping generator on complex seeds."
                )

            # Refund credits when ALL variants get filtered out post-API
            # (raw API returned variants but every one was rejected as
            # fragmented/flagged). Earlier refund branch only catches the case
            # where the API itself returned 0 variants — this catches the
            # "API returned variants but all were filtered" case so users
            # aren't charged 150 credits for zero usable output.
            if not enriched_variants:
                result_data["credits_refunded"] = True
                result_data["message"] = (
                    f"Lead optimization returned {len(variants)} raw variant(s) but all "
                    f"were filtered out (fragmented={fragmented_count}, flagged="
                    f"{len(variants[:max_variants]) - fragmented_count}). Common cause: "
                    f"complex fused polycyclic seeds (acridine, carbazole, naphthalene, "
                    f"xanthene) hit RDKit sanitization limits in the scaffold-hop "
                    f"generator. Try a simpler seed or use optimize_molecule (MolMIM) "
                    f"for property-directed edits. Credits refunded."
                )
                return ToolResult(
                    success=True,
                    data=result_data,
                    usage={"queries": 0, "tool": "lead_optimization", "_dynamic_credits": 0}
                )

            return ToolResult(
                success=True,
                data=result_data,
                usage={"queries": 1, "tool": "lead_optimization"}
            )

        except Exception as e:
            logger.exception(f"Error in lead_optimization: {e}")
            return ToolResult(success=False, error=f"Lead optimization failed: {str(e)}")

    # --- dock_molecules constants ---
    DOCK_BASE_CREDITS = 10
    DOCK_PER_MOLECULE_CREDITS = 5

    async def _execute_dock_molecules(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Two-phase docking: estimate (no token) → execute (with token).

        Single-molecule fast path: when smiles_list has exactly 1 molecule
        and no token is provided, execute directly. The two-phase handshake
        only adds value for batch docking where cost varies meaningfully
        (5-100 molecules, 25-510 credits). Single-molecule cost is fixed at
        15 credits — there's nothing to confirm.

        Skipping the handshake also works around ChatGPT's tool-call
        chaining gap, where the confirmation_token sometimes doesn't
        survive between Phase 1 and Phase 2 calls in the same turn.
        """
        confirmation_token = args.get("confirmation_token")
        if confirmation_token:
            return await self._execute_dock_run(args, confirmation_token, context=context)

        # Single-molecule fast path: skip estimate, execute directly
        smiles_list = args.get("smiles_list", [])
        if len(smiles_list) == 1:
            return await self._execute_dock_run_direct(args, context=context)

        return await self._execute_dock_estimate(args)

    async def _execute_dock_estimate(self, args: Dict[str, Any]) -> ToolResult:
        """Phase 1: Return cost estimate and confirmation token."""
        import hashlib

        smiles_list = args.get("smiles_list", [])
        protein_pdb_id = args.get("protein_pdb_id")

        if not smiles_list or not protein_pdb_id:
            return ToolResult(success=False, error="Missing required parameters: smiles_list, protein_pdb_id")

        if len(smiles_list) > 100:
            return ToolResult(success=False, error="Maximum 100 molecules per docking job")

        n = len(smiles_list)
        total_cost = self.DOCK_BASE_CREDITS + self.DOCK_PER_MOLECULE_CREDITS * n
        pdb_id = protein_pdb_id.upper()
        exhaustiveness = args.get("exhaustiveness", 16)
        num_modes = args.get("num_modes", 9)
        protonation_ph = args.get("protonation_ph", 7.4)
        funnel_id = args.get("funnel_id")
        # Reference ligand co-docking (Theo P0)
        reference_ligand_smiles = args.get("reference_ligand_smiles")
        enable_reference_docking = args.get("enable_reference_docking", True)

        # Generate confirmation token (HMAC-based, 10-min TTL)
        token_data = {
            "smiles_list": smiles_list,
            "protein_pdb_id": pdb_id,
            "exhaustiveness": exhaustiveness,
            "num_modes": num_modes,
            "protonation_ph": protonation_ph,
            "n_molecules": n,
            "cost": total_cost,
            "funnel_id": funnel_id,
            "reference_ligand_smiles": reference_ligand_smiles,
            "enable_reference_docking": enable_reference_docking,
            "ts": int(time.time()),
        }
        token_str = json.dumps(token_data, sort_keys=True)
        token_hash = hashlib.sha256(
            f"{token_str}:{self.internal_api_key}".encode()
        ).hexdigest()[:32]
        confirmation_token = f"dock_{token_hash}"

        await self._store_token(confirmation_token, token_data)

        return ToolResult(
            success=True,
            data={
                "phase": "estimate",
                "protein_pdb_id": pdb_id,
                "n_molecules": n,
                "exhaustiveness": exhaustiveness,
                "num_modes": num_modes,
                "protonation_ph": protonation_ph,
                "credit_breakdown": {
                    "base_cost": self.DOCK_BASE_CREDITS,
                    "per_molecule_cost": self.DOCK_PER_MOLECULE_CREDITS,
                    "molecule_count": n,
                    "total_credits": total_cost,
                },
                "confirmation_token": confirmation_token,
                "token_expires_in_seconds": 600,
                "message": (
                    f"Docking {n} molecule{'s' if n != 1 else ''} against {pdb_id} "
                    f"will cost {total_cost} credits "
                    f"({self.DOCK_BASE_CREDITS} base + {self.DOCK_PER_MOLECULE_CREDITS} × {n} molecules). "
                    f"Call dock_molecules again with this confirmation_token to proceed."
                ),
            },
            usage={"tool": "dock_molecules", "_dynamic_credits": 0},
        )

    async def _execute_dock_run_direct(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Single-molecule direct execution. Skips two-phase handshake.

        Used for `len(smiles_list) == 1` calls where the cost is fixed at
        15 credits and the confirmation_token round-trip adds no value.
        Builds a synthetic token_data dict from args and dispatches into
        the standard execution path.
        """
        smiles_list = args.get("smiles_list", [])
        protein_pdb_id = args.get("protein_pdb_id")

        if not smiles_list or not protein_pdb_id:
            return ToolResult(success=False, error="Missing required parameters: smiles_list, protein_pdb_id")

        if len(smiles_list) != 1:
            return ToolResult(success=False, error="Direct execution path is single-molecule only")

        n = 1
        token_data = {
            "smiles_list": smiles_list,
            "protein_pdb_id": protein_pdb_id.upper(),
            "exhaustiveness": args.get("exhaustiveness", 16),
            "num_modes": args.get("num_modes", 9),
            "protonation_ph": args.get("protonation_ph", 7.4),
            "n_molecules": n,
            "cost": self.DOCK_BASE_CREDITS + self.DOCK_PER_MOLECULE_CREDITS * n,
            "funnel_id": args.get("funnel_id"),
            "reference_ligand_smiles": args.get("reference_ligand_smiles"),
            "enable_reference_docking": args.get("enable_reference_docking", True),
            "ts": int(time.time()),
        }
        return await self._execute_dock_run(args, confirmation_token=None, context=context, _prevalidated_token_data=token_data)

    async def _execute_dock_run(
        self,
        args: Dict[str, Any],
        confirmation_token: Optional[str],
        context: Dict[str, Any] = None,
        _prevalidated_token_data: Optional[Dict[str, Any]] = None,
    ) -> ToolResult:
        """Phase 2: Validate token and execute docking.

        Pass `_prevalidated_token_data` to skip token validation (used by
        the single-molecule fast path in _execute_dock_run_direct).
        """
        import asyncio
        import uuid as _uuid

        if _prevalidated_token_data is not None:
            token_data = _prevalidated_token_data
        else:
            # Validate token
            token_data = await self._get_token(confirmation_token)
            if not token_data:
                return ToolResult(success=False, error="Invalid or expired confirmation token. Call dock_molecules without a token to get a new estimate.")

            # Mark token as used before execution (prevents replay)
            await self._mark_token_used(confirmation_token)

        smiles_list = token_data["smiles_list"]
        pdb_id = token_data["protein_pdb_id"]
        exhaustiveness = token_data["exhaustiveness"]
        num_modes = token_data["num_modes"]
        protonation_ph = token_data.get("protonation_ph", 7.4)
        total_cost = token_data["cost"]
        funnel_id = token_data.get("funnel_id")
        # Reference ligand co-docking (Theo P0)
        reference_ligand_smiles = token_data.get("reference_ligand_smiles")
        enable_reference_docking = token_data.get("enable_reference_docking", True)

        async def dock_single(ligand_smiles: str) -> Dict[str, Any]:
            """Dock a single molecule. Returns result dict or error dict."""
            try:
                dock_payload = {
                    "ligand_smiles": ligand_smiles,
                    "protein_pdb_id": pdb_id,
                    "exhaustiveness": exhaustiveness,
                    "num_poses": num_modes,
                    "protonation_ph": protonation_ph,
                    "auto_detect_binding_site": True,
                    "use_addie_reranking": True,
                    "enable_reference_docking": enable_reference_docking,
                }
                if reference_ligand_smiles:
                    dock_payload["reference_ligand_smiles"] = reference_ligand_smiles
                response = await self._call_service(
                    "autodock-gpu",
                    "/dock",
                    dock_payload,
                    # Reference docking doubles runtime — extend timeout from 120s to 240s
                    timeout=240.0 if enable_reference_docking else 120.0,
                )
                if response.status_code == 200:
                    data = response.json()
                    # Unwrap service envelope if present (autodock-gpu returns {service, status, data})
                    if "data" in data and isinstance(data["data"], dict):
                        data = data["data"]
                    data["smiles"] = ligand_smiles
                    return data
                # Capture error body for diagnostics
                try:
                    err_body = response.json().get("detail", response.text[:200])
                except Exception:
                    err_body = response.text[:200]
                return {"smiles": ligand_smiles, "error": f"HTTP {response.status_code}: {err_body}"}
            except Exception as e:
                return {"smiles": ligand_smiles, "error": str(e)}

        def _process_raw_results(raw_results, num_modes):
            """Process raw docking results into sorted results and failures."""
            results = []
            failures = []
            # Reference ligand metadata — same across all molecules in a batch
            # (same receptor/box). Take from first successful result.
            reference_affinity = None
            reference_smiles = None
            reference_source = None
            reference_error = None
            native_ligand = None
            reference_interactions = None  # PLIP contacts for reference pose (Theo P1)

            for r in raw_results:
                if r.get("error"):
                    failures.append(r)
                else:
                    affinity = (
                        r.get("binding_affinity_kcal")
                        or r.get("binding_affinity")
                        or r.get("best_affinity")
                        or r.get("best_score")
                        or r.get("score")
                    )
                    # Capture reference metadata from first successful result
                    # — populated independently so native_ligand + error are
                    # surfaced even when reference docking itself failed.
                    if native_ligand is None and r.get("native_ligand") is not None:
                        native_ligand = r.get("native_ligand")
                    if reference_smiles is None and r.get("reference_ligand_smiles"):
                        reference_smiles = r.get("reference_ligand_smiles")
                    if reference_source is None and r.get("reference_source"):
                        reference_source = r.get("reference_source")
                    if reference_affinity is None and r.get("reference_affinity_kcal") is not None:
                        reference_affinity = r.get("reference_affinity_kcal")
                    if reference_error is None and r.get("reference_error"):
                        reference_error = r.get("reference_error")
                    if reference_interactions is None and r.get("reference_interactions"):
                        reference_interactions = r.get("reference_interactions")

                    # Per-molecule delta_vs_reference (more useful than per-pose)
                    delta_ref = None
                    if affinity is not None and r.get("reference_affinity_kcal") is not None:
                        delta_ref = round(affinity - r["reference_affinity_kcal"], 2)

                    # Extract contacts from the top pose for this molecule
                    # (autodock-gpu now populates `contacts` per pose via PLIP)
                    top_pose_contacts: List[Dict[str, Any]] = []
                    pose_list = r.get("poses") or []
                    if isinstance(pose_list, list) and pose_list:
                        top = pose_list[0] if isinstance(pose_list[0], dict) else {}
                        top_pose_contacts = top.get("contacts") or []

                    # Interaction summary (Theo P1 — PLIP binding pose analysis)
                    interaction_summary = None
                    if top_pose_contacts:
                        counts: Dict[str, int] = {}
                        key_residues: List[str] = []
                        for ixn in top_pose_contacts:
                            itype = ixn.get("type", "unknown")
                            counts[itype] = counts.get(itype, 0) + 1
                            res = ixn.get("residue")
                            if res and res not in key_residues:
                                key_residues.append(res)
                        interaction_summary = {
                            "n_hbonds": counts.get("hbond", 0),
                            "n_hydrophobic": counts.get("hydrophobic", 0),
                            "n_salt_bridges": counts.get("salt_bridge", 0),
                            "n_pi_stacking": counts.get("pi_stacking", 0),
                            "n_pi_cation": counts.get("pi_cation", 0),
                            "n_halogen_bonds": counts.get("halogen", 0),
                            "n_water_bridges": counts.get("water_bridge", 0),
                            "n_metal_coord": counts.get("metal", 0),
                            "total_interactions": sum(counts.values()),
                            "key_residues": key_residues[:10],
                        }

                    results.append({
                        "smiles": r.get("smiles", ""),
                        "binding_affinity_kcal": affinity,
                        "poses": r.get("poses", r.get("num_poses", num_modes)),
                        "contacts": top_pose_contacts or r.get("contacts", []),
                        "interaction_summary": interaction_summary,
                        "binding_site_source": r.get("binding_site_source"),
                        "binding_site_score": r.get("binding_site_score"),
                        "delta_vs_reference_kcal": delta_ref,
                    })
            results.sort(key=lambda x: x.get("binding_affinity_kcal") or 0)
            best_aff = results[0].get("binding_affinity_kcal") if results else None
            for r in results:
                aff = r.get("binding_affinity_kcal")
                r["weak_binder"] = aff is not None and aff > -6.0
                r["delta_vs_best_kcal"] = round(aff - best_aff, 2) if aff and best_aff else None
            # Attach reference metadata to the results list as function attributes
            # (caller wraps into response; see return type)
            reference_meta = {
                "reference_affinity_kcal": reference_affinity,
                "reference_ligand_smiles": reference_smiles,
                "reference_source": reference_source,
                "reference_error": reference_error,
                "native_ligand": native_ligand,
                "reference_interactions": reference_interactions,
            }
            return results, failures, reference_meta

        # ---- Multi-molecule batch: async background docking ----
        if len(smiles_list) > 1:
            job_id = f"dock_batch_{_uuid.uuid4().hex[:12]}"

            # Store initial status in Redis
            r = await self._get_redis()
            if r:
                await r.hset(f"{self._redis_prefix}:dock:{job_id}", mapping={
                    "status": "processing",
                    "pdb_id": pdb_id,
                    "total": str(len(smiles_list)),
                    "completed_count": "0",
                    "exhaustiveness": str(exhaustiveness),
                    "num_modes": str(num_modes),
                    "total_cost": str(total_cost),
                    "smiles_list": json.dumps(smiles_list),
                })
                await r.expire(f"{self._redis_prefix}:dock:{job_id}", 3600)  # 1h TTL

            # Persist to async_jobs for dashboard visibility
            try:
                await self._execute_save_funnel_context({
                    "job_id": job_id,
                    "service": "autodock-gpu",
                    "context": {
                        "funnel_id": funnel_id,
                        "funnel_step": 7,
                        "tool": "dock_molecules",
                        "pdb_id": pdb_id,
                        "n_molecules": len(smiles_list),
                        "exhaustiveness": exhaustiveness,
                    }
                }, context=context)
            except Exception:
                pass

            # Spawn background task
            async def _dock_batch_bg():
                """Background: dock all molecules, update Redis with progress and results."""
                raw_results = []
                for idx, smi in enumerate(smiles_list):
                    result = await dock_single(smi)
                    raw_results.append(result)
                    # Update progress in Redis
                    rr = await self._get_redis()
                    if rr:
                        pct = int(((idx + 1) / len(smiles_list)) * 100)
                        await rr.hset(f"{self._redis_prefix}:dock:{job_id}", mapping={
                            "completed_count": str(idx + 1),
                            "progress": str(pct),
                        })

                results, failures, reference_meta = _process_raw_results(raw_results, num_modes)
                best = results[0].get("binding_affinity_kcal") if results else None
                mean_aff = round(
                    sum(r2["binding_affinity_kcal"] for r2 in results if r2.get("binding_affinity_kcal")) / max(len(results), 1), 2
                ) if results else None

                # Extract binding site provenance from first result (if service returns it)
                first_batch = results[0] if results else {}
                batch_site_source = first_batch.get("binding_site_source")
                batch_site_score = first_batch.get("binding_site_score")

                final_data = {
                    "phase": "completed",
                    "protein_pdb_id": pdb_id,
                    "binding_site_source": batch_site_source,
                    "binding_site_score": batch_site_score,
                    "protonation_ph": protonation_ph,
                    "exhaustiveness": exhaustiveness,
                    "num_modes": num_modes,
                    "molecules_docked": len(results),
                    "molecules_failed": len(failures),
                    "results": results,
                    "failures": failures if failures else None,
                    "best_affinity_kcal": best,
                    "mean_affinity_kcal": mean_aff,
                    "credits_consumed": total_cost,
                    # Reference ligand co-docking (Theo P0)
                    **reference_meta,
                }

                # Store final results in Redis
                rr = await self._get_redis()
                if rr:
                    await rr.hset(f"{self._redis_prefix}:dock:{job_id}", mapping={
                        "status": "completed",
                        "progress": "100",
                        "completed_count": str(len(smiles_list)),
                        "result_json": json.dumps(final_data),
                    })
                    await rr.expire(f"{self._redis_prefix}:dock:{job_id}", 86400)  # 24h TTL for results

                # Update async_jobs to completed
                try:
                    await self.client.patch(
                        f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                        json={"status": "completed", "result_data": final_data, "progress_pct": 100},
                        headers={"X-Admin-Key": self.dashboard_admin_key},
                        timeout=10.0,
                    )
                except Exception:
                    pass

                # Trigger email notification
                try:
                    await self.client.post(
                        f"{self.dashboard_url}/api/v1/jobs/{job_id}/notify",
                        headers={"X-Admin-Key": self.dashboard_admin_key},
                        timeout=10.0,
                    )
                except Exception:
                    pass  # Best-effort

            asyncio.create_task(_dock_batch_bg())

            # Estimate: ~30-45s per molecule (includes PDB fetch, prep, docking)
            est_seconds_per_mol = 40 if exhaustiveness >= 16 else 30
            est_minutes = max(2, round((len(smiles_list) * est_seconds_per_mol) / 60) + 1)

            return ToolResult(
                success=True,
                data={
                    "phase": "submitted",
                    "job_id": job_id,
                    "protein_pdb_id": pdb_id,
                    "n_molecules": len(smiles_list),
                    "exhaustiveness": exhaustiveness,
                    "status": "processing",
                    "estimated_minutes": est_minutes,
                    "message": (
                        f"Batch docking {len(smiles_list)} molecules against {pdb_id} "
                        f"(exhaustiveness={exhaustiveness}). "
                        f"Estimated time: ~{est_minutes} minutes. "
                        f"Do NOT check before {max(1, est_minutes - 1)} minute(s). "
                        f"Use get_job_status with job_id '{job_id}' to poll progress — keep polling every 30s until completed."
                    ),
                    "credits_consumed": total_cost,
                    "tool_suggestions": [
                        self._tool_suggestion("get_job_status", f"Check docking job {job_id} (wait ~{est_minutes} min, then poll every 30s)"),
                    ]
                },
                usage={"queries": len(smiles_list), "tool": "dock_molecules", "_dynamic_credits": total_cost},
            )

        # ---- Single molecule: synchronous (finishes within ~20-30s) ----
        try:
            raw_results = [await dock_single(smiles_list[0])]
            results, failures, reference_meta = _process_raw_results(raw_results, num_modes)
            best = results[0].get("binding_affinity_kcal") if results else None

            # Extract binding site provenance from first result (service-reported)
            first = results[0] if results else {}
            site_source = first.get("binding_site_source")
            site_score = first.get("binding_site_score")
            # protonation_ph comes from the token (user-requested value), not the
            # service response — the service may not echo it back yet.

            # Credit refund on total infra failure: if every dock attempt
            # failed (empty URL, ConnectTimeout, 5xx, etc.) the user gets
            # nothing useful — same posture as the 0-variants branch of
            # lead_optimization (L5033) and optimize_molecule (L5019).
            # Matters because phase 1 short-circuited to phase=completed
            # when AUTODOCK_GPU_URL was unset; user was
            # charged 15 credits for nothing. Don't bill on infra faults.
            all_failed = len(results) == 0 and len(failures) > 0
            charged_credits = 0 if all_failed else total_cost

            response_data = {
                "phase": "completed",
                "protein_pdb_id": pdb_id,
                "binding_site_source": site_source,
                "binding_site_score": site_score,
                "protonation_ph": protonation_ph,
                "exhaustiveness": exhaustiveness,
                "num_modes": num_modes,
                "molecules_docked": len(results),
                "molecules_failed": len(failures),
                "results": results,
                "failures": failures if failures else None,
                "best_affinity_kcal": best,
                "mean_affinity_kcal": best,
                "credits_consumed": charged_credits,
                # Reference ligand co-docking (Theo P0)
                **reference_meta,
                "tool_suggestions": [
                    self._tool_suggestion("run_molecular_dynamics", "Run MD simulation on top candidates"),
                    self._tool_suggestion("stratify_patients", "Assess clinical viability of top candidate"),
                ],
            }
            if all_failed:
                response_data["credits_refunded"] = True
                response_data["message"] = (
                    "Docking failed for all submitted molecules; no credits charged. "
                    "Inspect `failures[*].error` for the upstream cause "
                    "(likely an autodock-gpu service config or cold-start issue)."
                )

            return ToolResult(
                success=True,
                data=response_data,
                usage={"queries": len(smiles_list), "tool": "dock_molecules", "_dynamic_credits": charged_credits},
            )

        except Exception as e:
            logger.exception(f"Error in dock_molecules: {e}")
            return ToolResult(success=False, error=f"Docking failed: {str(e)}")

    async def _execute_audit_system(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Classify a structure via the in-process intake classifier.

        Free, synchronous (~5s), no GPU. Runs the classifier directly
        in novomcp instead of calling gromacs-md /audit (which is
        GPU-backed and scales to zero, causing 2-3 min cold-start
        timeouts on the first audit after idle). Same classifier code,
        no network hop. Results cached in Redis for 24h by pdb_id since
        classification is deterministic (OPM + MetalPDB don't change
        faster than monthly).

        Refactored May 2026: was calling gromacs-md /audit over HTTP,
        which inherited the GPU container's cold-start latency for a
        purely CPU-only classification call. Vendored the intake module
        from gromacs-md to novomcp/core/intake/ and call it directly.
        """
        pdb_id = args.get("pdb_id")
        pdb_content = args.get("pdb_content")

        if not pdb_id and not pdb_content:
            return ToolResult(success=False, error="Provide either pdb_id or pdb_content")

        # ── Redis cache (24h by pdb_id, skip for raw pdb_content) ──
        cache_key = f"audit:result:{pdb_id.upper()}" if pdb_id else None
        if cache_key:
            try:
                r = await self._get_redis()
                if r:
                    cached = await r.get(cache_key)
                    if cached:
                        logger.info(f"audit_system cache hit for {pdb_id}")
                        data = json.loads(cached)
                        return ToolResult(
                            success=True, data=data,
                            usage={"queries": 0, "tool": "audit_system"},
                        )
            except Exception:
                pass  # Cache miss or Redis unavailable — proceed normally

        # ── Run the classifier in-process ──
        try:
            try:
                from core.intake import classify_structure
            except ImportError as e:
                logger.error(f"intake module not available: {e}")
                return ToolResult(
                    success=False,
                    error=(
                        "Audit classifier not available in this deployment. "
                        "MDAnalysis or the intake module may be missing — "
                        "rebuild novomcp with the latest requirements.txt."
                    ),
                )

            # Fetch PDB from RCSB if only pdb_id given (mirrors gromacs-md
            # /audit behavior — same fetch happens before classification).
            resolved_content = pdb_content
            if pdb_id and not resolved_content:
                try:
                    import httpx as _httpx
                    rcsb_url = f"https://files.rcsb.org/download/{pdb_id.upper()}.pdb"
                    async with _httpx.AsyncClient(timeout=30.0) as client:
                        rcsb_resp = await client.get(rcsb_url)
                    if rcsb_resp.status_code != 200:
                        return ToolResult(
                            success=False,
                            error=f"Failed to fetch {pdb_id} from RCSB: HTTP {rcsb_resp.status_code}",
                        )
                    resolved_content = rcsb_resp.text
                except Exception as e:
                    return ToolResult(
                        success=False,
                        error=f"Failed to fetch {pdb_id} from RCSB: {str(e)[:200]}",
                    )

            redis_client = await self._get_redis()
            decision = await classify_structure(
                pdb_content=resolved_content,
                pdb_id=pdb_id,
                redis_client=redis_client,
            )

            # Mirror the /audit endpoint's response shape so the rest of this
            # function (reading `route`, `profile`, etc.) works unchanged.
            data = {
                "status": "audited",
                "would_route_to": decision.route,
                "suggested_branch": decision.suggested_branch,
                "primary_reason": decision.reasons[0] if decision.reasons else None,
                "reasons": decision.reasons,
                "profile": decision.profile.model_dump(),
            }

            route = data.get("would_route_to", "unknown")
            suggested = data.get("suggested_branch")
            profile = data.get("profile", {})
            primary_reason = data.get("primary_reason") or "no primary reason reported"

            # ── Build LLM-facing summary ──
            if route == "run_soluble":
                headline = f"{pdb_id or 'Structure'} passes intake — MD simulation supported (soluble, standard FF)."
                guidance = (
                    "This structure would be accepted by run_molecular_dynamics. "
                    "No membrane, no metals, no exotic cofactors detected."
                )
            elif route == "run_membrane":
                # run_membrane is a LIVE accept route (CHARMM36m + packmol-memgen bilayer).
                # Note in classifier.py:190 — "no longer a suggestion — it's the actual route."
                # Don't lump it with `refused` (future-branch) — that guidance is wrong here.
                headline = (
                    f"{pdb_id or 'Structure'} passes intake — MD simulation supported "
                    f"via the membrane branch (CHARMM36m + packmol-memgen bilayer)."
                )
                guidance = (
                    "This structure routes to the membrane MD branch automatically when "
                    "run_molecular_dynamics is called. Note: MM-GBSA is skipped for membrane "
                    "systems (the standard GB/PB solver is incorrect with a lipid bilayer); "
                    "ligand dynamics, RMSD, RMSF, and pose clustering still run normally."
                )
            else:  # refused
                headline = (
                    f"{pdb_id or 'Structure'} is outside the current pipeline scope. "
                    f"Reason: {primary_reason}"
                )
                branch_names = {
                    "mcpb_distal": "our upcoming MCPB distal-metal branch",
                    "qmmm_active_site": "our upcoming QM/MM active-site branch",
                    # charmm36m_membrane intentionally omitted — it's live (see
                    # run_membrane branch above). Listing it here would mis-name
                    # a shipped branch as "upcoming".
                }
                branch_human = branch_names.get(suggested, suggested) if suggested else "a future pipeline branch"
                guidance = (
                    f"This target needs {branch_human}, which is on the roadmap but not "
                    f"yet live. Do NOT call run_molecular_dynamics — it will refuse with "
                    f"the same reason. Present the refusal and suggest alternatives."
                )

            # ── Structured findings for inline display ──
            metal_sites = profile.get("metal_sites", [])
            findings = []
            if profile.get("is_membrane"):
                findings.append("Membrane protein (OPM hit)")
            for m in metal_sites:
                role = m.get("functional_role", "unknown")
                elem = m.get("element", "?")
                chain = m.get("chain")
                resno = m.get("residue_number")
                fp = m.get("fingerprint", "")
                loc = f"{elem}:{chain}@{resno}" if chain else f"{elem}@{resno}"
                findings.append(f"Metal {loc} ({role}): {fp}")
            if profile.get("heme_residues"):
                findings.append(f"Heme cofactor(s): {profile['heme_residues']}")
            if profile.get("fes_clusters"):
                findings.append(f"Iron-sulfur cluster(s): {profile['fes_clusters']}")

            # ── Tool suggestions (never empty — user feedback #1) ──
            if route == "run_soluble":
                suggestions = [
                    self._tool_suggestion(
                        "run_molecular_dynamics",
                        "Submit MD simulation (target passes pre-flight)",
                    ),
                ]
            else:
                suggestions = [
                    self._tool_suggestion(
                        "target_discovery",
                        "Find soluble alternative targets for this disease area",
                    ),
                    self._tool_suggestion(
                        "search_literature",
                        f"Research why {pdb_id or 'this target'} is a membrane/metalloprotein",
                    ),
                ]

            result_data = {
                "pdb_id": pdb_id,
                "would_route_to": route,
                "suggested_branch": suggested,
                "headline": headline,
                "guidance": guidance,
                "findings": findings,
                "reasons": data.get("reasons", []),
                "profile": profile,
                "tool_suggestions": suggestions,
            }

            # ── Redis cache write (24h) ──
            if cache_key:
                try:
                    r = await self._get_redis()
                    if r:
                        await r.set(cache_key, json.dumps(result_data), ex=86400)
                except Exception:
                    pass  # Non-fatal

            # ── Auto-log to funnel as stage_name="target_audit" (user feedback #3) ──
            try:
                await self._execute_save_funnel_stage({
                    "funnel_id": (context or {}).get("funnel_id", f"audit-{pdb_id or 'upload'}"),
                    "stage_index": 0,
                    "stage_name": "target_audit",
                    "stage_label": f"System Audit: {pdb_id or 'uploaded structure'}",
                    "tool_name": "audit_system",
                    "tool_arguments": json.dumps({"pdb_id": pdb_id}),
                    "results_summary": json.dumps({
                        "would_route_to": route,
                        "suggested_branch": suggested,
                        "primary_reason": primary_reason,
                        "is_membrane": profile.get("is_membrane", False),
                        "metal_count": len(metal_sites),
                    }),
                    "ai_recommendation": guidance,
                    "molecules_in": 1,
                    "molecules_out": 1 if route == "run_soluble" else 0,
                }, context=context)
            except Exception:
                pass  # Non-fatal — funnel logging is best-effort

            return ToolResult(
                success=True,
                data=result_data,
                usage={"queries": 1, "tool": "audit_system"},
            )

        except Exception as e:
            logger.exception(f"Error in audit_system: {e}")
            return ToolResult(success=False, error=f"Audit failed: {str(e)}")

    async def _execute_parameterize_metal(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Two-phase metal parameterization via MCPB.py — ASYNC JOB.

        Returns job_id immediately. Use get_job_status to poll for results.
        Refactored from sync (May 2026): the upstream gromacs-md call can
        take 1-5 minutes (cold start + MCPB.py processing + RESP charge
        derivation), which exceeded MCP transport timeouts on Claude/ChatGPT
        and caused user-visible TimeoutErrors even though the work was
        proceeding correctly. Async pattern eliminates this entirely.

        Phase 1 (no qm_log_content, no confirmation_token):
            Extracts the fragment, generates Gaussian .com input files.
            Returns .com file contents + confirmation_token.

        Phase 2 (qm_log_content + confirmation_token):
            Processes the QM .log from Phase 1's .com files.
            Returns .frcmod + .prep + GROMACS topology.
        """
        import asyncio
        import uuid as _uuid

        pdb_id = args.get("pdb_id")
        metal_resid = args.get("metal_resid")
        qm_log_content = args.get("qm_log_content")
        qm_file_id = args.get("qm_file_id")
        hessian_file_id = args.get("hessian_file_id")
        esp_file_id = args.get("esp_file_id")
        qm_software = args.get("qm_software", "gaussian")
        charge = args.get("charge", 0)
        multiplicity = args.get("multiplicity", 1)
        confirmation_token = args.get("confirmation_token")
        funnel_id = args.get("funnel_id")

        if not pdb_id or metal_resid is None:
            return ToolResult(
                success=False,
                error="Missing required parameters: pdb_id, metal_resid",
            )

        # Two-log Phase 2 is both-or-neither. Providing only one slot used to
        # fail the (hessian_file_id and esp_file_id) test below and fall through
        # to a Phase 1 run silently — surprising on a direct call. Require the
        # pair (or the single-log alternative) explicitly.
        if bool(hessian_file_id) != bool(esp_file_id):
            have, missing = (
                ("hessian_file_id", "esp_file_id") if hessian_file_id
                else ("esp_file_id", "hessian_file_id")
            )
            return ToolResult(
                success=False,
                error=(
                    f"Two-log Phase 2 needs BOTH hessian_file_id (small_fc/freq → "
                    f"Hessian) and esp_file_id (large_mk/Pop(MK) → ESP). You gave "
                    f"{have} but not {missing}. Provide both, or use qm_file_id / "
                    f"qm_log_content for a single combined log."
                ),
            )

        # Pre-flight cold-start check: parameterize_metal's background task
        # uses its own httpx.AsyncClient (multipart form upload, not the
        # JSON-only _call_service), so the warming envelope path in
        # _call_service does not fire automatically. Check gromacs-md
        # readiness here at the synchronous entry — if cold, kick off the
        # warmup and return the standard warming envelope without
        # submitting the job (no 50-credit charge, no orphan job_id).
        # Skip inside funnels where target_discovery already pre-warmed:
        # the scaler.is_warm() check is a single endpointslice GET that
        # returns true cheaply in that case.
        try:
            from core.k8s_scaler import get_scaler, ScalerError
            scaler = get_scaler()
            if not await scaler.is_warm("gromacs-md"):
                asyncio.create_task(scaler.kickstart_warmup("gromacs-md"))
                return self._warming_tool_result(
                    "parameterize_metal", "gromacs-md", self.GPU_COLD_START_SECONDS
                )
        except ScalerError as e:
            # Scaler unreachable (e.g., running outside cluster for unit
            # tests). Fall through — background task retry-on-cold-start
            # below will still handle it.
            logger.warning("parameterize_metal pre-flight scaler check failed: %s", e)
        except Exception as e:
            logger.debug("parameterize_metal pre-flight unexpected: %s", e)

        is_phase2 = bool(
            (qm_log_content or qm_file_id or (hessian_file_id and esp_file_id))
            and confirmation_token
        )
        phase_label = 2 if is_phase2 else 1

        # Generate job_id and seed Redis with submitted state
        job_id = f"mcpb_{_uuid.uuid4().hex[:12]}"
        redis_key = f"{self._redis_prefix}:mcpb:{job_id}"
        r = await self._get_redis()
        if r:
            await r.hset(redis_key, mapping={
                "status": "submitted",
                "phase": str(phase_label),
                "pdb_id": pdb_id.upper(),
                "metal_resid": str(metal_resid),
                "submitted_at": str(int(time.time())),
            })
            await r.expire(redis_key, 86400)  # 24h TTL

        # Persist to async_jobs for dashboard visibility
        try:
            await self._execute_save_funnel_context({
                "job_id": job_id,
                "service": "parameterize-metal",
                "context": {
                    "funnel_id": funnel_id,
                    "tool": "parameterize_metal",
                    "pdb_id": pdb_id,
                    "metal_resid": metal_resid,
                    "phase": phase_label,
                    "qm_software": qm_software,
                },
            }, context=context)
        except Exception:
            pass

        # Spawn background task to do the actual work
        async def _mcpb_bg():
            """Background: fetch file (Phase 2) → call gromacs-md → store result."""
            rr = await self._get_redis()
            user = context or {}
            org_id = user.get("org_id", "unknown")
            user_id = user.get("user_id") or user.get("sub") or "unknown"
            try:
                # Resolve the QM log input(s). Preferred Phase 2 path is the
                # TWO-log pair — a Hessian (small_fc/freq) log + an MK ESP
                # (large_mk) log — by file id. Legacy paths: a single combined
                # log via qm_file_id or inline qm_log_content.
                resolved_qm_log = qm_log_content
                resolved_hessian = None
                resolved_esp = None

                async def _fetch_log(fid: str) -> str:
                    fb = await self._file_client.fetch_file_content(fid, org_id)
                    await self._file_client.link_tool_call(
                        fid, org_id, "parameterize_metal", job_id=job_id,
                    )
                    return fb.decode("utf-8", errors="replace")

                if hessian_file_id and esp_file_id and not resolved_qm_log:
                    if not self._file_client:
                        await self._mcpb_store_error(
                            rr, redis_key, job_id,
                            "File upload service not configured. Use qm_log_content instead.",
                        )
                        return
                    try:
                        resolved_hessian = await _fetch_log(hessian_file_id)
                        resolved_esp = await _fetch_log(esp_file_id)
                        logger.info(
                            f"Fetched two-log QM pair: hessian={hessian_file_id} "
                            f"({len(resolved_hessian)} chars), esp={esp_file_id} "
                            f"({len(resolved_esp)} chars)"
                        )
                    except FileNotFoundError as e:
                        await self._mcpb_store_error(
                            rr, redis_key, job_id,
                            f"QM log file not found ({e}). Check hessian_file_id / "
                            f"esp_file_id and ensure both uploads completed.",
                        )
                        return
                    except Exception as e:
                        logger.exception(f"Failed to fetch QM log pair: {e}")
                        await self._mcpb_store_error(
                            rr, redis_key, job_id,
                            f"Failed to fetch uploaded QM logs: {str(e)[:300]}",
                        )
                        return
                elif qm_file_id and not resolved_qm_log:
                    if not self._file_client:
                        await self._mcpb_store_error(
                            rr, redis_key, job_id,
                            "File upload service not configured. Use qm_log_content instead.",
                        )
                        return
                    try:
                        file_bytes = await self._file_client.fetch_file_content(qm_file_id, org_id)
                        resolved_qm_log = file_bytes.decode("utf-8", errors="replace")
                        logger.info(
                            f"Fetched QM log from file intelligence layer: "
                            f"{qm_file_id} ({len(resolved_qm_log)} chars)"
                        )
                        await self._file_client.link_tool_call(
                            qm_file_id, org_id, "parameterize_metal", job_id=job_id,
                        )
                    except FileNotFoundError:
                        await self._mcpb_store_error(
                            rr, redis_key, job_id,
                            f"File {qm_file_id} not found. Check the file ID and ensure the upload completed.",
                        )
                        return
                    except Exception as e:
                        logger.exception(f"Failed to fetch file {qm_file_id}: {e}")
                        await self._mcpb_store_error(
                            rr, redis_key, job_id,
                            f"Failed to fetch uploaded file: {str(e)[:300]}",
                        )
                        return

                # Build upstream request
                service_url = self.service_urls.get("gromacs-md", "")
                if not service_url:
                    await self._mcpb_store_error(rr, redis_key, job_id, "gromacs-md service URL not configured")
                    return

                endpoint_url = f"{service_url}/parameterize-metal"
                api_key = self.service_api_keys.get("gromacs-md", self.internal_api_key)

                form_data = {
                    "pdb_id": pdb_id.upper(),
                    "metal_resid": str(metal_resid),
                    "qm_software": qm_software,
                    "charge": str(charge),
                    "multiplicity": str(multiplicity),
                }
                files = {}
                if is_phase2 and resolved_hessian and resolved_esp:
                    # Two-log pair: the service writes each to the MCPB-expected
                    # name (small_fc.log / large_mk.log) and validates per slot.
                    form_data["confirmation_token"] = confirmation_token
                    files["hessian_log"] = ("hessian.log", resolved_hessian.encode(), "text/plain")
                    files["esp_log"] = ("esp.log", resolved_esp.encode(), "text/plain")
                elif is_phase2 and resolved_qm_log:
                    form_data["confirmation_token"] = confirmation_token
                    file_ext = ".fchk" if "fchk" in qm_software.lower() else (
                        ".out" if qm_software.lower() == "orca" else ".log"
                    )
                    files["qm_log"] = (f"qm_output{file_ext}", resolved_qm_log.encode(), "text/plain")

                # Mark running
                if rr:
                    await rr.hset(redis_key, mapping={
                        "status": "running",
                        "started_at": str(int(time.time())),
                    })

                # Upstream call (gromacs-md is sync internally; we just absorb the wait here).
                # Belt-and-suspenders cold-start handling: the synchronous pre-flight
                # already short-circuits a cold dispatch with a warming envelope, but
                # there's a small race window where the pod could have scaled back to 0
                # between pre-flight and this dispatch. On ConnectError, block on
                # ensure_warm (up to 6 min) and retry once before failing the job.
                import httpx as _httpx
                async with _httpx.AsyncClient(timeout=600.0) as client:
                    async def _post():
                        return await client.post(
                            endpoint_url,
                            headers={"API-Key": api_key},
                            files=files if files else None,
                            data=form_data,
                        )
                    try:
                        response = await _post()
                    except _httpx.ConnectError as ce:
                        logger.info(
                            "mcpb %s: gromacs-md cold at dispatch — ensuring warm + retrying",
                            job_id,
                        )
                        try:
                            from core.k8s_scaler import get_scaler
                            await get_scaler().ensure_warm("gromacs-md", timeout_s=360)
                        except Exception as warm_err:
                            await self._mcpb_store_error(
                                rr, redis_key, job_id,
                                f"gromacs-md warmup failed: {str(warm_err)[:200]}",
                            )
                            return
                        try:
                            response = await _post()
                        except Exception as retry_err:
                            await self._mcpb_store_error(
                                rr, redis_key, job_id,
                                f"gromacs-md retry after warmup failed: {str(retry_err)[:200]}",
                            )
                            return

                if response.status_code not in (200, 400):
                    await self._mcpb_store_error(
                        rr, redis_key, job_id,
                        f"Parameterization service error: {response.status_code} — {response.text[:500]}",
                    )
                    return

                data = response.json()
                # Process response into final result_data using shared helper
                result_data = self._mcpb_process_response(data, pdb_id, metal_resid)

                # Store final result
                if rr:
                    await rr.hset(redis_key, mapping={
                        "status": "completed" if result_data.get("success") else "failed",
                        "completed_at": str(int(time.time())),
                        "result_json": json.dumps(result_data),
                    })

                # Update async_jobs
                try:
                    await self.client.patch(
                        f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                        json={
                            "status": "completed" if result_data.get("success") else "failed",
                            "result_data": result_data,
                            "progress_pct": 100,
                        },
                        headers={"X-Admin-Key": self.dashboard_admin_key},
                        timeout=10.0,
                    )
                except Exception:
                    pass

                # On a successful Phase 2, register the FF deliverables
                # (.frcmod/.prep/.top/.gro) as child files of the input log so
                # the provenance DAG + derived_files populate. Option A: parent
                # is the Hessian log (file→file); the two-input fan-in is carried
                # by the job. Fail-soft — never fails the job.
                if result_data.get("success"):
                    parent_for_outputs = hessian_file_id or qm_file_id
                    if parent_for_outputs:
                        await self._register_mcpb_outputs(
                            result_data, parent_for_outputs, org_id, user_id, job_id,
                        )

            except Exception as e:
                logger.exception(f"parameterize_metal background task failed for {job_id}: {e}")
                await self._mcpb_store_error(rr, redis_key, job_id, f"Background task failed: {str(e)[:300]}")

        asyncio.create_task(_mcpb_bg())

        # Estimate: ~1-2 min for Phase 1 (fragment extraction + .com generation),
        # ~2-5 min for Phase 2 (Gaussian log parsing + RESP + Seminario + topology)
        est_minutes = 5 if is_phase2 else 2

        return ToolResult(
            success=True,
            data={
                "job_id": job_id,
                "status": "submitted",
                "phase": phase_label,
                "pdb_id": pdb_id.upper(),
                "metal_resid": metal_resid,
                "estimated_minutes": est_minutes,
                "message": (
                    f"Parameterization Phase {phase_label} submitted for {pdb_id.upper()} "
                    f"residue {metal_resid}. "
                    f"Estimated runtime: ~{est_minutes} minutes. "
                    f"Use get_job_status with job_id '{job_id}' — keep polling every 60s until completed."
                ),
                "tool_suggestions": [
                    self._tool_suggestion(
                        "get_job_status",
                        f"Check parameterize_metal job {job_id} (poll every 60s)",
                    ),
                ],
            },
            usage={"queries": 1, "tool": "parameterize_metal"},
        )

    async def _register_mcpb_outputs(
        self,
        result_data: Dict[str, Any],
        parent_file_id: str,
        org_id: str,
        user_id: str,
        job_id: str,
    ) -> None:
        """Register Phase 2 FF deliverables as child files of the input log.

        Pulls the output ZIP that gromacs-md wrote to result_data["blob_url"]
        (an S3 URL), and registers each .frcmod/.prep/.top/.gro
        member via the file intelligence layer with parent_file_id pointing at
        the input (Option A: the Hessian log) and linked to the Phase 2 job.
        This is what makes the input's `derived_files` populate and completes
        the provenance DAG (QM logs → FF params).

        Fail-soft: the FF params already live in blob_url; provenance
        registration must never fail the job. Logs a warning on any error.
        """
        if not (self._file_client and parent_file_id):
            return
        blob_url = (result_data or {}).get("blob_url") or ""
        if not blob_url.startswith("s3://"):
            return
        # File types worth registering individually (skip AMBER binaries +
        # tleap input that live in the ZIP but aren't user deliverables).
        ext_to_type = {
            ".frcmod": "frcmod",
            ".prep": "prep",
            ".top": "gromacs_top",
            ".gro": "gromacs_gro",
        }
        try:
            import io
            import zipfile

            _, _, rest = blob_url.partition("s3://")
            bucket, _, key = rest.partition("/")
            obj = self._file_client._s3.get_object(Bucket=bucket, Key=key)
            zip_bytes = obj["Body"].read()

            registered: List[str] = []
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                for name in zf.namelist():
                    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
                    file_type = ext_to_type.get(ext)
                    if not file_type:
                        continue
                    child = await self._file_client.create_output_file(
                        parent_file_id=parent_file_id,
                        org_id=org_id,
                        user_id=user_id,
                        filename=name,
                        file_type=file_type,
                        content=zf.read(name),
                        metadata={"source_job": job_id, "tool": "parameterize_metal"},
                    )
                    await self._file_client.link_tool_call(
                        child["file_id"], org_id, "parameterize_metal", job_id=job_id,
                    )
                    registered.append(child["file_id"])

            if registered:
                logger.info(
                    f"Registered {len(registered)} MCPB output file(s) under "
                    f"parent {parent_file_id} (job {job_id}): {registered}"
                )
        except Exception as e:
            logger.warning(
                f"MCPB output registration failed (job {job_id}, non-fatal): {e}"
            )

    async def _mcpb_store_error(self, redis_client, redis_key: str, job_id: str, error_msg: str) -> None:
        """Helper: persist a parameterize_metal failure to Redis + async_jobs."""
        if redis_client:
            await redis_client.hset(redis_key, mapping={
                "status": "failed",
                "completed_at": str(int(time.time())),
                "error": error_msg,
                "result_json": json.dumps({"success": False, "error": error_msg}),
            })
        try:
            await self.client.patch(
                f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                json={"status": "failed", "result_data": {"error": error_msg}, "progress_pct": 100},
                headers={"X-Admin-Key": self.dashboard_admin_key},
                timeout=10.0,
            )
        except Exception:
            pass

    def _mcpb_process_response(self, data: Dict[str, Any], pdb_id: str, metal_resid: Any) -> Dict[str, Any]:
        """Helper: normalize gromacs-md /parameterize-metal response into final result_data.

        Used by the background task to convert the upstream response shape into
        the structure the LLM and viewer consume via get_job_status. Returns a
        plain dict (not ToolResult) — the get_job_status read path wraps it.
        """
        status = data.get("status", "unknown")

        if status == "invalid_qm_log":
            validation = data.get("validation") or data.get("qm_validation", {})
            # Collect warnings from the single-log shape (top-level) AND the
            # two-log shape (per-slot nested under hessian_log / esp_log), so the
            # error pinpoints which slot is wrong instead of a generic sentence.
            warnings = list(validation.get("warnings", []) or [])
            for slot in ("hessian_log", "esp_log"):
                sv = validation.get(slot)
                if isinstance(sv, dict) and not sv.get("valid"):
                    warnings += [f"[{slot}] {w}" for w in (sv.get("warnings") or [])]
            # The service already builds a per-slot message for the two-log pair;
            # prefer it, then fall back to assembled warnings, then a generic hint.
            detail = data.get("message") or " ".join(warnings)
            error = (
                f"{detail} (Phase 2 needs a Hessian log [freq] and an MK ESP log "
                f"[Pop(MK)] — as hessian_file_id + esp_file_id, or one combined log.)"
                if detail else
                "QM log validation failed. MCPB.py requires both a Hessian (freq "
                "keyword) and ESP charges (Merz-Kollman). Provide hessian_file_id + "
                "esp_file_id."
            )
            return {
                "success": False,
                "status": "invalid_qm_log",
                "error": error,
                "validation": validation,
            }

        if status in ("mcpb_failed", "mcpb_step1_failed"):
            diag = data.get("diagnostic", {})
            diag_str = ""
            if diag:
                metal_line = diag.get("metal_pdb_line", "")
                cols_name = diag.get("cols_12_16_atomname", "")
                cols_elem = diag.get("cols_76_78_element", "")
                mcpb_in = diag.get("mcpb_input", "")
                mol2 = diag.get("ion_mol2", "")
                diag_str = (
                    f"\n\n--- DIAGNOSTIC ---"
                    f"\nMetal PDB line: [{metal_line}]"
                    f"\n  Atom name (12-16): [{cols_name}]"
                    f"\n  Element (76-78):   [{cols_elem}]"
                    f"\nMCPB.py input:\n{mcpb_in}"
                    f"\nIon mol2:\n{mol2}"
                )
            return {
                "success": False,
                "status": status,
                "error": f"MCPB.py failed: {data.get('error', 'unknown error')}{diag_str}",
                "diagnostic": diag,
            }

        if status == "phase1_complete":
            token = data.get("confirmation_token", "")
            # If gromacs-md auto-resolved a wrong metal_resid (Option C from
            # QM-FF-BRIDGE-COMPLETION.md Item 2), echo the upstream's
            # corrected value AND the correction notice so the LLM updates
            # its mental model + any subsequent calls use the right id.
            upstream_metal_resid = data.get("metal_resid", metal_resid)
            metal_resid_correction = data.get("metal_resid_correction")
            return {
                "success": True,
                "status": "phase1_complete",
                "phase": 1,
                "compound_id": data.get("compound_id"),
                "pdb_id": pdb_id.upper(),
                "metal_resid": upstream_metal_resid,
                "metal_resid_correction": metal_resid_correction,
                "metal_element": data.get("metal_element"),
                "confirmation_token": token,
                "token_expires_in_seconds": data.get("token_expires_in_seconds", 86400),
                "qm_input_files": data.get("qm_input_files", []),
                "files": data.get("files", {}),
                "blob_url": data.get("blob_url"),
                "message": data.get("message", "Phase 1 complete"),
                "instructions": (
                    f"MCPB.py has generated Gaussian input files (.com) for the "
                    f"metal coordination fragment. The user must:\n"
                    f"1. Download the .com files (or copy from 'files' field)\n"
                    f"2. Run Gaussian on each .com file (especially _small_fc.com "
                    f"and _large_mk.com)\n"
                    f"3. Call parameterize_metal again with:\n"
                    f"   - confirmation_token: '{token}'\n"
                    f"   - qm_log_content: contents of the .log file (or qm_file_id from upload)\n"
                    f"Token expires in 24 hours."
                ),
            }

        if status == "success":
            return {
                "success": True,
                "status": "success",
                "phase": 2,
                "compound_id": data.get("compound_id"),
                "pdb_id": pdb_id.upper(),
                "metal_resid": metal_resid,
                "metal_element": data.get("metal_element"),
                "output_files": data.get("outputs", []),
                "blob_url": data.get("blob_url"),
                "gromacs": data.get("gromacs", {}),
                "message": data.get("message", "Parameterization complete"),
            }

        return {
            "success": False,
            "status": status,
            "error": f"Unexpected status from parameterize-metal: {status}. Full response: {json.dumps(data)[:500]}",
        }

    async def _execute_run_molecular_dynamics(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Run GPU molecular dynamics simulation via GROMACS.

        Step 6 (2026-05-14): dispatches to the `gromacs-md-job` Container
        Apps Job instead of POSTing to the long-running `gromacs-md`
        k8s Job. The Job survives GPU node preemption (spot interrupts) via the
        replicaRetryLimit=1 + Redis config persistence + Blob checkpoint

        Flow:
          1. Resolve inputs + estimate runtime + build dedupe_key
          2. If an in-flight job with the same hash exists, return its job_id
          3. Pre-create SQL row via save_funnel_context + PATCH dedupe_key
          4. LPUSH {job_id, config} onto novomcp:gromacs:job_queue
          5. ARM Jobs.start against gromacs-md-job (empty body — config goes
          6. PATCH execution_id back into SQL for cancellation support
        """
        import hashlib
        import uuid

        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        pdb_id = args.get("pdb_id")  # optional — omit for ligand-only
        duration_ns = float(args.get("duration_ns", 10))
        temperature = float(args.get("temperature", 300))
        pressure = float(args.get("pressure", 1.0))
        funnel_id = args.get("funnel_id")
        intent = args.get("intent")  # v3 quality: scientific intent for adequacy grading
        _valid_intents = {"smoke_test", "equilibration_only", "pose_stability", "mm_gbsa"}
        if intent is not None and intent not in _valid_intents:
            return ToolResult(
                success=False,
                error=f"Invalid intent '{intent}'. Must be one of: {sorted(_valid_intents)}",
            )
        adaptive_equilibration = bool(args.get("adaptive_equilibration", False))

        # Estimate calibrated against typical A100 throughput (~3 min/ns plus
        # ~12 min equilibration/prep/analysis). Used for the user message
        # only — the executor mints its own progress and the dashboard's
        # watchdog uses 2x this as the stale threshold.
        estimated_minutes = round(duration_ns * 3) + 12

        # ── Idempotency: hash canonical inputs to a 16-char dedupe_key ─────
        # An identical retry storm should join the in-flight job rather than
        dedupe_payload = f"{smiles}|{pdb_id or ''}|{duration_ns}|{temperature}|{pressure}"
        dedupe_key = hashlib.sha256(dedupe_payload.encode()).hexdigest()[:16]

        existing = await self._find_active_md_job_by_dedupe(dedupe_key)
        if existing:
            return ToolResult(
                success=True,
                data={
                    "job_id": existing["job_id"],
                    "execution_id": existing.get("execution_id"),
                    "status": "already_running",
                    "service": "gromacs-md",
                    "smiles": smiles,
                    "pdb_id": pdb_id,
                    "duration_ns": duration_ns,
                    "temperature": temperature,
                    "dedupe_key": dedupe_key,
                    "message": (
                        f"An identical MD simulation is already in flight (job_id {existing['job_id']}, "
                        f"current status: {existing.get('status', 'queued')}). Polling that one instead "
                        f"of starting a duplicate. Tell the user this is the same simulation they "
                        f"previously submitted — use get_job_status with job_id '{existing['job_id']}'."
                    ),
                    "tool_suggestions": ["get_job_status"],
                },
                usage={"tool": "run_molecular_dynamics"},
            )

        # ── New submission ────────────────────────────────────────────────
        # job_id prefix `gro_` matches what the existing get_job_status
        # routing already uses to dispatch to gromacs-md state lookups.
        job_id = f"gro_{datetime.now().strftime('%Y%m%d-%H%M%S')}_{abs(hash(smiles[:20])) % 100000:05d}"

        # compound_id derivation mirrors gromacs-md/routes/simulate.py:81-83
        # so funnel_audit + analysis files use the same naming convention as
        # the legacy HTTP path.
        compound_id = (
            args.get("compound_id")
            or (f"{pdb_id}-ligand" if pdb_id else f"mol-{uuid.uuid4().hex[:8]}")
        )

        # Dispatch config — keys match what run_md_job.py reads from the
        # queue message (see run_md_job.py main_async()). ligand_smiles is
        # the canonical key on the executor side; we keep `smiles` as the
        # MCP input parameter for back-compat with prior callers.
        config: Dict[str, Any] = {
            "compound_id": compound_id,
            "ligand_smiles": smiles,
            "pdb_id": pdb_id.upper() if pdb_id else None,
            "duration_ns": duration_ns,
            "temperature": temperature,
            "pressure": pressure,
            "pipeline": "soluble",  # membrane resume out of scope per design doc Part 2
        }
        if intent is not None:
            config["intent"] = intent
        if adaptive_equilibration:
            config["adaptive_equilibration"] = True
        # Observability: log the dispatch config keys so we can see in
        # production logs whether MCP-connector args were dropped before
        # reaching us. Quiet keys only — no SMILES/secrets.
        logger.info(
            f"MD dispatch {job_id} config keys={sorted(config.keys())} "
            f"intent={intent!r} adaptive_equilibration={adaptive_equilibration}"
        )

        # Pre-create SQL row so the executor's first PATCH has something to
        # UPDATE. Without this, status writes silently no-op (UPDATE 0 rows).
        try:
            await self._execute_save_funnel_context({
                "job_id": job_id,
                "service": "gromacs-md",
                "context": {
                    "funnel_id": funnel_id,
                    "funnel_step": 8,
                    "tool": "run_molecular_dynamics",
                    "smiles": smiles,
                    "pdb_id": pdb_id,
                    "compound_id": compound_id,
                    "duration_ns": duration_ns,
                    "temperature": temperature,
                    "dedupe_key": dedupe_key,
                    "estimated_minutes": estimated_minutes,
                }
            }, context=context)
            await self._dashboard_patch_job(job_id, {
                "status": "queued",
                "dedupe_key": dedupe_key,
                "progress_pct": 0,
                "progress_message": "Submitting k8s Job to EKS",
            })
        except Exception as e:
            logger.warning(f"Pre-create SQL row for MD {job_id} failed (continuing): {e}")

        # ── LPUSH dispatch message onto Redis queue ───────────────────────
        # Historical context: ARM Jobs.start template-override was rejected on the legacy Azure
        # executor BLPOPs on startup; on retry after preemption it falls
        # back to the persisted config key.
        try:
            r = await self._get_redis()
            if not r:
                raise RuntimeError("Redis unavailable — cannot dispatch MD job")
            dispatch_message = json.dumps({
                "job_id": job_id,
                "config": config,
            })
            await r.lpush("novomcp:gromacs:job_queue", dispatch_message)
            await r.expire("novomcp:gromacs:job_queue", 86400)
        except Exception as e:
            err_str = str(e)[:500]
            logger.exception(f"Redis LPUSH for MD job {job_id} failed: {e}")
            await self._dashboard_patch_job(job_id, {
                "status": "failed",
                "progress_pct": 0,
                "progress_message": "Failed to dispatch to MD queue",
                "error_message": f"Redis dispatch failed: {err_str}",
            })
            return ToolResult(success=False, error=f"Failed to dispatch MD job: {err_str[:300]}")

        # ── Start the k8s Job (formerly Azure CA Job) ──────────────────────
        try:
            azure_jobs = self._get_azure_jobs()
            execution_name = await azure_jobs.start_job_execution(
                job_name=self._gromacs_md_job_name,
                execution_id=job_id,
                env_overrides={
                    "MD_JOB_ID": job_id,
                    "MD_CONFIG": json.dumps(config),
                },
            )
        except Exception as e:
            err_str = str(e)[:500]
            logger.exception(f"k8s Job start failed for MD {job_id}: {e}")
            # Roll back the queue push so an orphan job_id doesn't get
            # picked up by an unrelated future execution.
            try:
                await r.lrem("novomcp:gromacs:job_queue", 1, dispatch_message)
            except Exception:
                pass
            await self._dashboard_patch_job(job_id, {
                "status": "failed",
                "progress_pct": 0,
                "progress_message": "Job submission to EKS failed",
                "error_message": f"k8s Job submission failed: {err_str}",
            })
            return ToolResult(success=False, error=f"Failed to start MD job: {err_str[:300]}")

        # Persist execution_id so cancel_job can stop this run later.
        await self._dashboard_patch_job(job_id, {
            "status": "queued",
            "execution_id": execution_name,
            "progress_pct": 1,
            "progress_message": "k8s Job submitted, GPU node provisioning",
        })

        return ToolResult(
            success=True,
            data={
                "job_id": job_id,
                "execution_id": execution_name,
                "status": "queued",
                "service": "gromacs-md",
                "smiles": smiles,
                "pdb_id": pdb_id,
                "compound_id": compound_id,
                "duration_ns": duration_ns,
                "temperature": temperature,
                "estimated_minutes": estimated_minutes,
                "dedupe_key": dedupe_key,
                "method": f"GROMACS soluble pipeline, {duration_ns}ns at {temperature}K",
                "message": (
                    f"MD simulation submitted as k8s Job {execution_name}. "
                    f"Ligand: {smiles[:30]}... "
                    f"{'Target: ' + pdb_id + '. ' if pdb_id else '(ligand-only). '}"
                    f"Simulation: {duration_ns}ns at {temperature}K. "
                    f"Estimated runtime: ~{estimated_minutes} minutes. "
                    f"Do NOT check status before {max(5, estimated_minutes - 5)} minutes. "
                    f"Use get_job_status with job_id '{job_id}' — poll every 60s until completed."
                ),
                "tool_suggestions": [
                    self._tool_suggestion(
                        "get_job_status",
                        f"Check MD job {job_id} (wait ~{estimated_minutes} min, then poll every 60s)"
                    ),
                    self._tool_suggestion(
                        "stratify_patients",
                        "Run pharmacogenomic analysis after MD completes"
                    ),
                ]
            },
            usage={"queries": 1, "tool": "run_molecular_dynamics"}
        )

    async def _execute_generate_dynamics(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Generate conformational ensemble via AlphaFlow/ESMFlow."""
        pdb_id = args.get("pdb_id")
        pdb_data = args.get("pdb_data")
        sequence = args.get("sequence")
        n_frames = args.get("n_frames", 50)
        funnel_id = args.get("funnel_id")

        if not pdb_id and not pdb_data and not sequence:
            return ToolResult(success=False, error="Provide one of: pdb_id, pdb_data, or sequence")

        payload = {"n_frames": n_frames}
        if pdb_id:
            payload["pdb_id"] = pdb_id.upper()
        elif pdb_data:
            payload["pdb_data"] = pdb_data
        elif sequence:
            payload["sequence"] = sequence

        try:
            response = await self._call_service(
                "alphaflow",
                "/generate",
                payload,
                timeout=120.0,
            )

            if response.status_code == 503:
                return ToolResult(
                    success=False,
                    error=(
                        "AlphaFlow model is still warming up (cold start). "
                        "This is normal after a deployment or period of inactivity. "
                        "Tell the user to wait 2 minutes, then try again. "
                        "Do NOT report this as a permanent failure."
                    )
                )
            if response.status_code not in (200, 202):
                detail = response.text[:300] if response.text else "no detail"
                return ToolResult(success=False, error=f"AlphaFlow service error ({response.status_code}): {detail}")

            data = response.json()
            job_id = data.get("job_id", "")
            estimated_minutes = data.get("estimated_runtime_minutes", 3)

            # Persist to async_jobs
            try:
                await self._execute_save_funnel_context({
                    "job_id": job_id,
                    "service": "alphaflow",
                    "context": {
                        "funnel_id": funnel_id,
                        "funnel_step": 9,
                        "tool": "generate_dynamics",
                        "pdb_id": pdb_id,
                        "n_frames": n_frames,
                    }
                }, context=context)
            except Exception:
                pass

            return ToolResult(
                success=True,
                data={
                    "job_id": job_id,
                    "status": "submitted",
                    "pdb_id": pdb_id,
                    "n_frames": n_frames,
                    "estimated_minutes": estimated_minutes,
                    "message": (
                        f"Conformational ensemble generation submitted"
                        f"{' for ' + pdb_id if pdb_id else ''}. "
                        f"Generating {n_frames} conformations. "
                        f"Estimated runtime: ~{estimated_minutes} minutes. "
                        f"Use get_job_status with job_id '{job_id}' — poll every 60s until completed. "
                        f"Results will include animated trajectory, per-residue RMSF, and PCA of motions."
                    ),
                    "tool_suggestions": [
                        self._tool_suggestion("get_job_status",
                            f"Check dynamics job {job_id} (wait ~{estimated_minutes} min, then poll every 60s)"),
                    ]
                },
                usage={"queries": 1, "tool": "generate_dynamics"}
            )

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout,
                httpx.PoolTimeout, httpx.ConnectTimeout) as e:
            # Connection-level failures during AlphaFlow cold start: the container
            # is scaling up (Container Apps consumption GPU) and the model (ESMFlow,
            # ~1.5GB) takes ~50s to load before the FastAPI app accepts connections.
            # Request attempts that land during that window get connection-refused
            # or timeout exceptions — NOT a 503 from the app itself. Surface the
            # same friendly cold-start message the 503 path uses instead of a
            # generic "Dynamics generation failed:" with an opaque exception string.
            logger.info(f"generate_dynamics cold-start retry window: {type(e).__name__}: {e}")
            return ToolResult(
                success=False,
                error=(
                    "AlphaFlow is cold-starting (the GPU container is scaling up "
                    "and loading the ESMFlow model, ~2-3 min). This is normal "
                    "after a period of inactivity. Tell the user to wait 2 minutes, "
                    "then try again. Do NOT report this as a permanent failure — "
                    "the service is healthy, just warming up."
                )
            )
        except Exception as e:
            logger.exception(f"Error in generate_dynamics: {e}")
            return ToolResult(success=False, error=f"Dynamics generation failed: {str(e)}")

    # =========================================================================
    # Background Job Poller
    # =========================================================================

    async def start_job_poller(self):
        """Background loop that polls active async jobs every 60s.

        Detects completion/failure, updates async_jobs status, and triggers
        email notifications — so users don't need to manually poll.
        """
        import asyncio
        logger.info("Job poller: Starting background poller (60s interval)")
        while True:
            try:
                await asyncio.sleep(60)
                await self._poll_active_jobs()
            except asyncio.CancelledError:
                logger.info("Job poller: Cancelled — shutting down")
                break
            except Exception as e:
                logger.error(f"Job poller: Unexpected error (non-fatal): {e}")

    async def _poll_active_jobs(self):
        """Query dashboard-aggregator for active jobs and poll their services."""
        try:
            # Query submitted and running jobs separately (endpoint doesn't support comma-separated)
            all_jobs = []
            for status_filter in ("submitted", "running"):
                try:
                    resp = await self.client.get(
                        f"{self.dashboard_url}/api/v1/jobs?status={status_filter}",
                        headers={"X-Admin-Key": self.dashboard_admin_key},
                        timeout=15.0,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, dict):
                            data = data.get("jobs", data.get("data", []))
                        if isinstance(data, list):
                            all_jobs.extend(data)
                except Exception:
                    pass
            jobs = all_jobs
            if not jobs:
                return

            logger.info(f"Job poller: Checking {len(jobs)} active job(s)")

            for job in jobs:
                try:
                    await self._poll_single_job(job)
                except Exception as e:
                    logger.warning(f"Job poller: Failed to poll job {job.get('job_id', '?')}: {e}")

        except Exception as e:
            logger.warning(f"Job poller: Failed to fetch active jobs: {e}")

    async def _poll_single_job(self, job: Dict[str, Any]):
        """Poll a single job's service for status and update async_jobs if terminal."""
        job_id = job.get("job_id", "")
        service = job.get("service", "")
        if not job_id or not service:
            return

        # dock_batch_ jobs are managed via Redis, not by calling autodock-gpu
        if job_id.startswith("dock_batch_"):
            try:
                r = await self._get_redis()
                if not r:
                    return
                redis_key = f"{self._redis_prefix}:dock:{job_id}"
                job_data = await r.hgetall(redis_key)
                if not job_data:
                    return
                job_data = {(k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v) for k, v in job_data.items()}
                status = job_data.get("status", "")
                if status == "completed":
                    result_json = job_data.get("result_json", "{}")
                    try:
                        await self.client.patch(
                            f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                            json={"status": "completed", "result_data": json.loads(result_json), "progress_pct": 100},
                            headers={"X-Admin-Key": self.dashboard_admin_key},
                            timeout=10.0,
                        )
                    except Exception:
                        pass
                elif status == "processing":
                    progress = int(job_data.get("progress", 0))
                    try:
                        await self.client.patch(
                            f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                            json={"status": "running", "progress_pct": progress},
                            headers={"X-Admin-Key": self.dashboard_admin_key},
                            timeout=10.0,
                        )
                    except Exception:
                        pass
            except Exception:
                pass
            return

        # Map service name to _call_service name and endpoint.
        # Keys must match what save_funnel_context / async_jobs stores as `service`.
        # Aliases handled: "alphaflow" and "af" both route to alphaflow's /status endpoint.
        service_map = {
            "gromacs-md": ("gromacs-md", f"/status/{job_id}"),
            "openfold3": ("openfold3", f"/result/{job_id}"),
            "lead-optimization": ("lead-optimization", f"/jobs/{job_id}/status"),
            "autodock-gpu": ("autodock-gpu", f"/jobs/{job_id}/status"),
            # AlphaFlow conformational dynamics — was missing from this map,
            # causing every af_* job to silently skip polling and stay stuck
            # in "submitted" forever in the Pipeline Jobs dashboard.
            "alphaflow": ("alphaflow", f"/status/{job_id}"),
            "alpha-flow": ("alphaflow", f"/status/{job_id}"),
        }
        if service not in service_map:
            # Infer service from job_id prefix when async_jobs didn't record a
            # matching service name (e.g. older rows that predate the mapping).
            if job_id.startswith("af_"):
                service_map_key = "alphaflow"
                svc_name, endpoint = service_map["alphaflow"]
            else:
                return
        else:
            svc_name, endpoint = service_map[service]

        try:
            poll_resp = await self._call_service(svc_name, endpoint, {}, method="GET", timeout=15.0)
        except Exception:
            return  # Service unreachable — try next cycle

        if poll_resp.status_code != 200:
            return

        data = poll_resp.json()
        if "data" in data and isinstance(data["data"], dict):
            data = data["data"]
        status = data.get("status", "")

        # Update progress if running
        if status in ("running", "processing"):
            raw_progress = data.get("progress")
            if isinstance(raw_progress, dict):
                progress = raw_progress.get("percentage", 0)
            else:
                progress = raw_progress
            if progress is not None:
                try:
                    await self.client.patch(
                        f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                        json={"status": "running", "progress_pct": progress},
                        headers={"X-Admin-Key": self.dashboard_admin_key},
                        timeout=10.0,
                    )
                except Exception:
                    pass
            return

        if status == "completed":
            results = data.get("results") or data.get("data", {})
            # Try fetching full results for gromacs
            if service == "gromacs-md" and (not results or results == {}):
                try:
                    result_resp = await self._call_service(
                        "gromacs-md", f"/results/{job_id}", {}, method="GET", timeout=15.0
                    )
                    if result_resp.status_code == 200:
                        result_data = result_resp.json()
                        if "data" in result_data and isinstance(result_data["data"], dict):
                            result_data = result_data["data"]
                        results = result_data.get("result", result_data)
                except Exception:
                    pass

            # Update async_jobs to completed
            try:
                await self.client.patch(
                    f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                    json={"status": "completed", "result_data": results, "progress_pct": 100},
                    headers={"X-Admin-Key": self.dashboard_admin_key},
                    timeout=10.0,
                )
            except Exception as e:
                logger.warning(f"Job poller: Failed to update job {job_id} to completed: {e}")

            # Trigger email notification
            try:
                await self.client.post(
                    f"{self.dashboard_url}/api/v1/jobs/{job_id}/notify",
                    headers={"X-Admin-Key": self.dashboard_admin_key},
                    timeout=10.0,
                )
                logger.info(f"Job poller: Notified completion for {job_id}")
            except Exception:
                pass  # Best-effort

        elif status in ("failed", "error"):
            error_msg = data.get("message") or data.get("error") or "Unknown error"
            try:
                await self.client.patch(
                    f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                    json={"status": "failed", "error_message": error_msg},
                    headers={"X-Admin-Key": self.dashboard_admin_key},
                    timeout=10.0,
                )
                logger.info(f"Job poller: Marked job {job_id} as failed: {error_msg}")
            except Exception:
                pass

    # =========================================================================
    # Funnel Context Persistence Executors
    # =========================================================================

    async def _execute_save_funnel_context(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Save funnel state for an async job via dashboard-aggregator."""
        job_id = args.get("job_id")
        funnel_context = args.get("context", {})
        service = args.get("service", "")

        if not job_id:
            return ToolResult(success=False, error="Missing required parameter: job_id")
        if not self.dashboard_url:
            return ToolResult(
                success=False,
                error="save_funnel_context requires a funnel-persistence backend (set FUNNEL_BACKEND_URL, aliased from DASHBOARD_AGGREGATOR_URL for backwards compat). Async job persistence isn't wired in local mode.",
                usage={"queries": 0, "tool": "save_funnel_context"},
            )

        try:
            # Route through dashboard-aggregator (owns identity-db)
            response = await self.client.post(
                f"{self.dashboard_url}/api/v1/jobs/{job_id}/context",
                json={
                    "service": service,
                    "funnel_context": funnel_context,
                    "user_id": (context or {}).get("user_id", ""),
                    "org_id": (context or {}).get("org_id", ""),
                },
                headers={"X-Admin-Key": self.dashboard_admin_key},
                timeout=15.0,
            )

            if response.status_code in (200, 201):
                return ToolResult(
                    success=True,
                    data={"job_id": job_id, "saved": True},
                    usage={"queries": 1, "tool": "save_funnel_context"}
                )

            logger.warning(f"save_funnel_context: dashboard-aggregator returned {response.status_code}")
            return ToolResult(
                success=True,
                data={"job_id": job_id, "saved": False, "error": f"HTTP {response.status_code}"},
                usage={"queries": 0, "tool": "save_funnel_context"}
            )

        except Exception as e:
            logger.error(f"Failed to save funnel context for {job_id}: {e}")
            # Non-fatal — log but don't block the caller
            return ToolResult(
                success=True,
                data={"job_id": job_id, "saved": False, "error": str(e)},
                usage={"queries": 0, "tool": "save_funnel_context"}
            )

    async def _execute_get_funnel_context(self, args: Dict[str, Any]) -> ToolResult:
        """Retrieve saved funnel context for an async job via dashboard-aggregator."""
        job_id = args.get("job_id")
        if not job_id:
            return ToolResult(success=False, error="Missing required parameter: job_id")

        try:
            response = await self.client.get(
                f"{self.dashboard_url}/api/v1/jobs/{job_id}/context",
                headers={"X-Admin-Key": self.dashboard_admin_key},
                timeout=15.0,
            )

            if response.status_code == 200:
                data = response.json()
                return ToolResult(
                    success=True,
                    data=data,
                    usage={"queries": 1, "tool": "get_funnel_context"}
                )

            if response.status_code == 404:
                return ToolResult(
                    success=False,
                    error=f"No funnel context found for job '{job_id}'. The job may not have been submitted through the MCP funnel."
                )

            return ToolResult(
                success=False,
                error=f"Failed to retrieve funnel context: HTTP {response.status_code}"
            )

        except Exception as e:
            logger.exception(f"Error retrieving funnel context for {job_id}: {e}")
            return ToolResult(success=False, error=f"Failed to retrieve funnel context: {str(e)}")

    # =========================================================================
    # QM Compute (xTB, CREST, Strain) via novomcp-qm
    # =========================================================================

    async def _execute_run_qm_calculation(self, args: Dict[str, Any]) -> ToolResult:
        """Run xTB quantum chemistry calculation."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        # Accept both `calculation` (schema canonical) and `calculation_type`
        # (used in older docs/tests). Without this, sending `calculation_type`
        # silently fell through to the "optimize" default, ignoring the user's
        # intent and dropping `optimized_xyz` from the response.
        calculation = (
            args.get("calculation")
            or args.get("calculation_type")
            or "optimize"
        )
        solvent = args.get("solvent", "water") if calculation == "solvation" else args.get("solvent")

        payload: Dict[str, Any] = {"smiles": smiles, "calculation": calculation}
        if solvent:
            payload["solvent"] = solvent
        if args.get("charge"):
            payload["charge"] = args["charge"]
        if args.get("uhf"):
            payload["uhf"] = args["uhf"]
        if args.get("xyz_input"):
            payload["xyz_input"] = args["xyz_input"]

        try:
            response = await self._call_service(
                "novomcp-qm",
                "/api/qm-calculate",
                payload,
                timeout=120.0,
            )

            if response.status_code != 200:
                detail = response.text[:300]
                return ToolResult(success=False, error=f"QM calculation failed ({response.status_code}): {detail}")

            data = response.json()
            result_data = {
                "smiles": data["smiles"],
                "calculation_type": calculation,
                "method": data["method"],
                "energy_hartree": data.get("energy_hartree"),
                "energy_kcal_mol": data.get("energy_kcal_mol"),
                "homo_lumo_gap_eV": data.get("gap_ev"),
                "dipole_debye": data.get("dipole_debye"),
                "wall_time_seconds": data.get("wall_time_seconds"),
            }
            if data.get("solvation_energy_kcal_mol") is not None:
                result_data["solvation_energy_kcal_mol"] = data["solvation_energy_kcal_mol"]
            if data.get("homo_ev") is not None:
                result_data["homo_eV"] = data["homo_ev"]
                result_data["lumo_eV"] = data["lumo_ev"]
            # Pass optimized geometry through so callers can feed it into
            # downstream tools (vertical IP, Hessian at fixed geometry, etc.).
            if data.get("optimized_xyz"):
                result_data["optimized_xyz"] = data["optimized_xyz"]

            return ToolResult(success=True, data=result_data, usage={"tool": "run_qm_calculation"})
        except Exception as e:
            logger.exception(f"QM calculation error for {smiles}: {e}")
            return ToolResult(success=False, error=f"QM calculation failed: {str(e)}")

    async def _execute_run_qm_hessian(self, args: Dict[str, Any]) -> ToolResult:
        """Run xTB Hessian for vibrational frequencies and thermochemistry."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        payload: Dict[str, Any] = {"smiles": smiles}
        for key in ("charge", "uhf", "solvent", "temperature", "xyz_input", "optimize_first"):
            if args.get(key) is not None:
                payload[key] = args[key]

        try:
            response = await self._call_service(
                "novomcp-qm",
                "/api/qm-hessian",
                payload,
                timeout=180.0,
            )

            if response.status_code != 200:
                detail = response.text[:300]
                return ToolResult(success=False, error=f"Hessian calculation failed ({response.status_code}): {detail}")

            data = response.json()
            result_data = {
                "smiles": data["smiles"],
                "method": data["method"],
                "energy_hartree": data.get("energy_hartree"),
                "energy_kcal_mol": data.get("energy_kcal_mol"),
                "zpe_kcal_mol": data.get("zpe_kcal_mol"),
                "enthalpy_correction_kcal_mol": data.get("enthalpy_correction_kcal_mol"),
                "gibbs_correction_kcal_mol": data.get("gibbs_correction_kcal_mol"),
                "entropy_cal_mol_k": data.get("entropy_cal_mol_k"),
                "temperature_k": data.get("temperature_k"),
                "n_imaginary": data.get("n_imaginary", 0),
                "is_true_minimum": data.get("is_true_minimum", True),
                "wall_time_seconds": data.get("wall_time_seconds"),
            }
            if data.get("frequencies_cm1"):
                result_data["n_vibrational_modes"] = len(data["frequencies_cm1"])
                result_data["frequencies_cm1"] = data["frequencies_cm1"]
            if data.get("imaginary_frequencies_cm1"):
                result_data["imaginary_frequencies_cm1"] = data["imaginary_frequencies_cm1"]
            if data.get("optimized_xyz"):
                result_data["optimized_xyz"] = data["optimized_xyz"]

            return ToolResult(success=True, data=result_data, usage={"tool": "run_qm_hessian"})
        except Exception as e:
            logger.exception(f"Hessian calculation error for {smiles}: {e}")
            return ToolResult(success=False, error=f"Hessian calculation failed: {str(e)}")

    async def _execute_predict_frontier_orbitals(self, args: Dict[str, Any]) -> ToolResult:
        """Predict frontier orbital properties for OLED/optoelectronics screening."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        payload: Dict[str, Any] = {"smiles": smiles}
        if args.get("solvent"):
            payload["solvent"] = args["solvent"]

        try:
            response = await self._call_service(
                "novomcp-properties",
                "/api/predict-frontier-orbitals",
                payload,
                timeout=90.0,
            )

            if response.status_code != 200:
                detail = response.text[:300]
                return ToolResult(success=False, error=f"Frontier orbital prediction failed ({response.status_code}): {detail}")

            data = response.json()
            return ToolResult(success=True, data=data, usage={"tool": "predict_frontier_orbitals"})
        except Exception as e:
            logger.exception(f"Frontier orbital prediction error for {smiles}: {e}")
            return ToolResult(success=False, error=f"Frontier orbital prediction failed: {str(e)}")

    async def _execute_run_excited_states(self, args: Dict[str, Any]) -> ToolResult:
        """Run sTDA-xTB excited state calculation."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        payload: Dict[str, Any] = {"smiles": smiles}
        for key in ("charge", "num_states", "xyz_input"):
            if args.get(key) is not None:
                payload[key] = args[key]

        try:
            response = await self._call_service(
                "novomcp-qm",
                "/api/qm-excited-states",
                payload,
                timeout=120.0,
            )

            if response.status_code != 200:
                detail = response.text[:300]
                return ToolResult(success=False, error=f"Excited state calculation failed ({response.status_code}): {detail}")

            data = response.json()
            return ToolResult(success=True, data=data, usage={"tool": "run_excited_states"})
        except Exception as e:
            logger.exception(f"Excited state error for {smiles}: {e}")
            return ToolResult(success=False, error=f"Excited state calculation failed: {str(e)}")

    async def _execute_predict_redox_potential(self, args: Dict[str, Any]) -> ToolResult:
        """Predict oxidation/reduction potentials via xTB thermodynamic cycle."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        payload: Dict[str, Any] = {"smiles": smiles}
        if args.get("solvent"):
            payload["solvent"] = args["solvent"]
        if args.get("reference_electrode"):
            payload["reference_electrode"] = args["reference_electrode"]

        try:
            response = await self._call_service(
                "novomcp-properties",
                "/api/predict-redox-potential",
                payload,
                timeout=180.0,  # 3 sequential xTB optimizations
            )

            if response.status_code != 200:
                detail = response.text[:300]
                return ToolResult(success=False, error=f"Redox prediction failed ({response.status_code}): {detail}")

            data = response.json()
            return ToolResult(success=True, data=data, usage={"tool": "predict_redox_potential"})
        except Exception as e:
            logger.exception(f"Redox prediction error for {smiles}: {e}")
            return ToolResult(success=False, error=f"Redox prediction failed: {str(e)}")

    async def _execute_predict_reaction_thermodynamics(self, args: Dict[str, Any]) -> ToolResult:
        """Predict reaction thermodynamics (ΔG, ΔH, K_eq)."""
        reactant_smiles = args.get("reactant_smiles")
        product_smiles = args.get("product_smiles")
        if not reactant_smiles or not product_smiles:
            return ToolResult(success=False, error="Missing required parameters: reactant_smiles and product_smiles")

        payload: Dict[str, Any] = {
            "reactant_smiles": reactant_smiles,
            "product_smiles": product_smiles,
        }
        if args.get("solvent"):
            payload["solvent"] = args["solvent"]
        if args.get("temperature") is not None:
            payload["temperature"] = args["temperature"]

        try:
            response = await self._call_service(
                "novomcp-properties",
                "/api/predict-reaction-thermo",
                payload,
                timeout=300.0,  # Hessian per species, 2+ species
            )

            if response.status_code != 200:
                detail = response.text[:300]
                return ToolResult(success=False, error=f"Reaction thermo failed ({response.status_code}): {detail}")

            data = response.json()
            return ToolResult(success=True, data=data, usage={"tool": "predict_reaction_thermodynamics"})
        except Exception as e:
            logger.exception(f"Reaction thermo error: {e}")
            return ToolResult(success=False, error=f"Reaction thermo failed: {str(e)}")

    async def _execute_find_transition_state(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Find transition state via NEB (async job)."""
        reactant_xyz = args.get("reactant_xyz")
        product_xyz = args.get("product_xyz")
        if not reactant_xyz or not product_xyz:
            return ToolResult(success=False, error="Missing required parameters: reactant_xyz and product_xyz. Optimize both geometries first with run_qm_hessian (optimize_first=true).")

        payload: Dict[str, Any] = {
            "reactant_xyz": reactant_xyz,
            "product_xyz": product_xyz,
        }
        for key in ("n_images", "charge", "uhf", "solvent", "fmax", "max_steps", "climb"):
            if args.get(key) is not None:
                payload[key] = args[key]

        n_images = payload.get("n_images", 8)
        max_steps = payload.get("max_steps", 200)

        try:
            response = await self._call_service(
                "novomcp-neb",
                "/api/qm-neb",
                payload,
                timeout=30.0,  # Just enough for submission (async)
            )

            if response.status_code not in (200, 202):
                detail = response.text[:300]
                return ToolResult(success=False, error=f"NEB submission failed ({response.status_code}): {detail}")

            data = response.json()

            # Async job response (has job_id)
            if "job_id" in data:
                job_id = data["job_id"]
                estimated_minutes = data.get("estimated_minutes", max(1, n_images * max_steps // 500))

                # Register with dashboard-aggregator for /jobs page
                try:
                    await self._execute_save_funnel_context({
                        "job_id": job_id,
                        "service": "novomcp-neb",
                        "context": {
                            "tool": "find_transition_state",
                            "n_images": n_images,
                            "max_steps": max_steps,
                            "method": data.get("method", "GFN2-xTB CI-NEB"),
                        }
                    }, context=context)
                except Exception:
                    pass  # Non-fatal

                return ToolResult(
                    success=True,
                    data={
                        "job_id": job_id,
                        "status": "submitted",
                        "service": "novomcp-neb",
                        "n_images": n_images,
                        "max_steps": max_steps,
                        "method": data.get("method", "GFN2-xTB CI-NEB"),
                        "estimated_minutes": estimated_minutes,
                        "message": (
                            f"NEB transition state search submitted. "
                            f"{n_images} images, max {max_steps} steps. "
                            f"Estimated runtime: ~{estimated_minutes} minutes. "
                            f"IMPORTANT: Tell the user to wait ~{estimated_minutes} minutes, then ask them to say "
                            f"'check job {job_id}' so you can poll. Do NOT auto-poll in a loop — "
                            f"you will exhaust your tool call budget. Poll at most 2 times per turn."
                        ),
                        "tool_suggestions": ["get_job_status"],
                    },
                    usage={"tool": "find_transition_state"},
                )

            # Synchronous response (fallback when Redis unavailable on service)
            return ToolResult(success=True, data=data, usage={"tool": "find_transition_state"})

        except Exception as e:
            logger.exception(f"NEB error: {e}")
            return ToolResult(success=False, error=f"NEB transition state search failed: {str(e)}")

    def _get_azure_jobs(self):
        """Lazy-init the long-running job control plane.

        Creates k8s Jobs in (port from Azure Container Apps Jobs / ARM REST done 2026-06-01)
        `default` via the BatchV1 API. Name kept for call-site stability
        across the Azure→AWS migration. Fails fast at first use if the
        kubernetes package isn't available or in-cluster auth doesn't load.
        """
        if self._azure_jobs is None:
            from core.k8s_jobs import K8sJobsClient

            self._azure_jobs = K8sJobsClient(
            )
        return self._azure_jobs

    async def _dashboard_patch_job(self, job_id: str, payload: Dict[str, Any]) -> bool:
        """PATCH async_jobs row. Best-effort — never raises; returns True on success."""
        if not self.dashboard_admin_key:
            return False
        try:
            resp = await self.client.patch(
                f"{self.dashboard_url}/api/v1/jobs/{job_id}/status",
                json=payload,
                headers={"X-Admin-Key": self.dashboard_admin_key},
                timeout=10.0,
            )
            return resp.status_code == 200
        except Exception as e:
            logger.debug(f"dashboard PATCH {job_id} failed: {e}")
            return False


    async def _dedupe_candidate_if_live(self, candidate: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Return the candidate only if it is genuinely still in flight.

        The dashboard 'active' flag can be stale: a k8s Job that died (e.g. a
        pmx hybrid-topology failure on a hard transformation) writes its
        terminal status to Redis, but SQL isn't always backfilled — leaving the
        row 'queued'. Without this cross-check a dead job masquerades as
        in-flight and BLOCKS resubmission (the "honest-failure path" bug: an
        advisor/customer sees a phantom queued job and cannot retry). Redis is
        the executor's authoritative terminal write, so consult it.
        """
        if not candidate:
            return None
        cj_id = candidate.get("job_id")
        try:
            r = await self._get_redis()
            if r and cj_id:
                rd = await r.hgetall(f"{self._redis_prefix}:job:{cj_id}")
                rstatus = rd.get("status") if rd else None
                if isinstance(rstatus, bytes):
                    rstatus = rstatus.decode()
                if rstatus in ("failed", "completed", "cancelled"):
                    logger.info(
                        f"dedupe: candidate {cj_id} is terminal in Redis "
                        f"('{rstatus}') — treating as not in-flight, allowing resubmit"
                    )
                    return None
        except Exception:
            pass
        return candidate

    async def _find_active_md_job_by_dedupe(self, dedupe_key: str) -> Optional[Dict[str, Any]]:
        """Look up an in-flight gromacs-md job with the same input hash.

        Step 6 (2026-05-14): used by _execute_run_molecular_dynamics to
        short-circuit duplicate MD submissions now that MD dispatches to
        a k8s Job (where re-running an identical 10ns
        protein-ligand sim wastes ~30 min of A100 time per duplicate).
        """
        if not self.dashboard_admin_key or not dedupe_key:
            return None
        try:
            resp = await self.client.get(
                f"{self.dashboard_url}/api/v1/jobs",
                params={
                    "service": "gromacs-md",
                    "dedupe_key": dedupe_key,
                    "active": "true",
                    "limit": 1,
                },
                headers={"X-Admin-Key": self.dashboard_admin_key},
                timeout=10.0,
            )
            if resp.status_code != 200:
                return None
            jobs = resp.json().get("jobs") or []
            return await self._dedupe_candidate_if_live(jobs[0]) if jobs else None
        except Exception as e:
            logger.debug(f"MD dedupe lookup failed: {e}")
            return None

    async def _execute_cancel_job(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Cancel a running/queued async job (k8s-Job-backed services on EKS).

        Supports gromacs-md (added 2026-05-14 with Step 6 of the MD durability
        migration) and other k8s-Job-backed services.

        Flow:
          1. Look up the row in async_jobs by job_id (need execution_id + service + status).
          2. If status is already terminal (completed/failed/cancelled), return that.
          3. Issue ARM `Jobs.stop` against the captured execution_id, scoped to
             the Job resource for that service.
          4. PATCH status='cancelling' so subsequent get_job_status polls show progress.
             The executor's SIGTERM handler writes the final status — note that
             mid-engine SIGTERM surfaces as 'failed' with the engine's error
             rather than 'cancelled' (intentional).
        """
        job_id = args.get("job_id")
        if not job_id:
            return ToolResult(success=False, error="Missing required parameter: job_id")
        reason = (args.get("reason") or "Cancelled by user")[:300]

        if not self.dashboard_admin_key:
            return ToolResult(success=False, error="Dashboard auth not configured — cannot cancel jobs")

        # Pull current state. Don't issue the ARM stop blind — if the job is
        # already done, calling stop is wasted work and slightly confusing in
        # the audit trail (a successful cancel against a completed job).
        # Phase 3.2: pass X-Org-Id so cross-tenant cancel attempts 404 (we
        # treat 404 as "doesn't exist" rather than 403 to avoid leaking
        # job_id existence across orgs).
        cancel_headers = {"X-Admin-Key": self.dashboard_admin_key}
        caller_org_id = context.get("org_id") if context else None
        if caller_org_id:
            cancel_headers["X-Org-Id"] = caller_org_id
        try:
            ctx_resp = await self.client.get(
                f"{self.dashboard_url}/api/v1/jobs/{job_id}/context",
                headers=cancel_headers,
                timeout=10.0,
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Failed to read job state: {str(e)[:200]}")

        if ctx_resp.status_code == 404:
            return ToolResult(success=False, error=f"Job not found: {job_id}")
        if ctx_resp.status_code != 200:
            return ToolResult(success=False, error=f"Dashboard read failed (HTTP {ctx_resp.status_code})")

        ctx = ctx_resp.json()
        service = ctx.get("service", "")
        current_status = ctx.get("status", "unknown")
        execution_id = ctx.get("execution_id")

        # Redis carries the CURRENT container's execution_name (rewritten by
        # the executor on every status write). When k8s spawns a retry pod
        # execution (replicaRetryLimit=1 after preemption), SQL still has the
        # ORIGINAL execution_id from novomcp's submission PATCH, while
        # Redis has the retry's. Prefer Redis when it disagrees — otherwise
        # ARM stop would target a dead execution and the retry would keep
        # running with no SIGTERM. Best-effort; falls back to SQL on any
        # Redis error.
        try:
            r = await self._get_redis()
            if r:
                redis_exec = await r.hget(
                    f"{self._redis_prefix}:job:{job_id}", "execution_id"
                )
                if redis_exec:
                    if isinstance(redis_exec, bytes):
                        redis_exec = redis_exec.decode()
                    if redis_exec and redis_exec != "local" and redis_exec != execution_id:
                        logger.info(
                            f"cancel_job {job_id}: SQL execution_id={execution_id} "
                            f"differs from Redis={redis_exec}; targeting Redis value "
                            f"(retry container scenario)"
                        )
                        execution_id = redis_exec
        except Exception as e:
            logger.debug(f"cancel_job Redis execution_id lookup failed for {job_id}: {e}")

        if current_status in ("completed", "failed", "cancelled"):
            return ToolResult(
                success=True,
                data={
                    "job_id": job_id,
                    "status": current_status,
                    "cancelled": current_status == "cancelled",
                    "message": f"Job is already in terminal state '{current_status}' — cancel is a no-op.",
                },
                usage={"tool": "cancel_job"},
            )

        if current_status == "cancelling":
            return ToolResult(
                success=True,
                data={
                    "job_id": job_id,
                    "status": "cancelling",
                    "cancelled": False,
                    "message": "Job is already in 'cancelling' state. Poll get_job_status to see when it lands on 'cancelled'.",
                },
                usage={"tool": "cancel_job"},
            )

        # Map service → k8s Job resource name. Adding a service
        # here requires its executor to handle SIGTERM (write status, cleanup
        # checkpoints) and its dispatch path to populate execution_id in SQL.
        cancellable_services = {
            "gromacs-md": self._gromacs_md_job_name,
        }
        if service not in cancellable_services:
            return ToolResult(
                success=False,
                error=(
                    f"cancel_job supports services {sorted(cancellable_services)} "
                    f"(got service='{service}'). Other services need executor-side "
                    f"SIGTERM cleanup before they can be safely cancelled."
                ),
            )
        target_job_name = cancellable_services[service]

        if not execution_id:
            # Job was queued but never started (Jobs.start didn't return) — we
            # can mark it cancelled directly without an ARM stop, since there's
            # no running execution to abort.
            await self._dashboard_patch_job(job_id, {
                "status": "cancelled",
                "progress_pct": 0,
                "progress_message": f"Cancelled before k8s Job pod started: {reason}",
                "error_message": f"Cancelled before execution: {reason}",
            })
            return ToolResult(
                success=True,
                data={
                    "job_id": job_id,
                    "status": "cancelled",
                    "cancelled": True,
                    "message": "Job was cancelled before its k8s Job pod started. No GPU time consumed.",
                },
                usage={"tool": "cancel_job"},
            )

        # Issue k8s Job delete (which SIGTERMs the pod). The 5min stop
        # timeout is generous — the k8s API usually acks in <2s. If the
        # API is unhealthy we surface the error rather than patching SQL
        # into a state that doesn't reflect the cluster's actual state.
        # `azure_jobs` is the historical name kept for ABI compat — the
        # underlying client was repointed at k8s after the 2026-06-01 port.
        try:
            azure_jobs = self._get_azure_jobs()
            await azure_jobs.stop_job_execution(
                job_name=target_job_name,
                execution_name=execution_id,
            )
        except Exception as e:
            err_str = str(e)[:300]
            logger.warning(f"k8s Job delete for {job_id} (execution {execution_id}) failed: {e}")
            # The k8s Job is likely already terminated (404 = not found, the
            # pod already exited). Still update SQL/Redis to cancelled so the
            # dashboard doesn't show a stale "running" state indefinitely.
            await self._dashboard_patch_job(job_id, {
                "status": "cancelled",
                "progress_message": f"Cancelled: {reason} (k8s Job already terminated)",
                "error_message": f"Cancelled: {reason}",
            })
            return ToolResult(
                success=True,
                data={
                    "job_id": job_id,
                    "status": "cancelled",
                    "cancelled": True,
                    "message": (
                        f"k8s Job was already terminated (delete returned: {err_str[:100]}). "
                        f"Job marked cancelled in SQL."
                    ),
                },
                usage={"tool": "cancel_job"},
            )

        # Mark cancelling — the executor's SIGTERM trap writes 'cancelled' to
        # Redis once the signal propagates; novomcp's next get_job_status
        # poll persists that to SQL.
        await self._dashboard_patch_job(job_id, {
            "status": "cancelling",
            "progress_pct": ctx.get("progress_pct", 0),
            "progress_message": f"Cancellation issued: {reason}",
            "error_message": reason,
        })

        return ToolResult(
            success=True,
            data={
                "job_id": job_id,
                "execution_id": execution_id,
                "status": "cancelling",
                "cancelled": False,
                "previous_status": current_status,
                "service": service,
                "message": (
                    f"Cancellation issued for {service} job {job_id} (k8s Job {execution_id}). "
                    f"Status is now 'cancelling'. The executor will catch SIGTERM and write the final "
                    f"status — typically 'failed' with the engine's last error if SIGTERM hit mid-stage, "
                    f"or 'cancelled' if it hit between stages. Poll get_job_status in ~30s to confirm."
                ),
                "tool_suggestions": ["get_job_status"],
            },
            usage={"tool": "cancel_job"},
        )



    async def _execute_run_conformer_search(self, args: Dict[str, Any], context: Dict[str, Any] = None) -> ToolResult:
        """Run CREST/RDKit conformer search (async job)."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        max_conformers = args.get("max_conformers", 20)
        quick = args.get("quick", False)

        try:
            response = await self._call_service(
                "novomcp-qm",
                "/api/conformer-search",
                {
                    "smiles": smiles,
                    "max_conformers": max_conformers,
                    "energy_window": args.get("energy_window", 6.0),
                    "quick": quick,
                },
                timeout=30.0,  # Just enough for submission (async) or quick sync
            )

            if response.status_code != 200:
                detail = response.text[:300]
                return ToolResult(success=False, error=f"Conformer search failed ({response.status_code}): {detail}")

            data = response.json()

            # Async job response (has job_id)
            if "job_id" in data:
                job_id = data["job_id"]
                method = data.get("method", "CREST")

                # Register with dashboard-aggregator so job appears in list_jobs.
                # Passing the dispatcher's context forwards user_id + org_id; without
                # those fields the dashboard's POST /api/v1/jobs/{job_id}/context
                # insert lands in async_jobs without ownership, and the /jobs dashboard
                # (filtered by org_id) can't see it. Matches the pattern in
                # _execute_generate_dynamics and _execute_find_transition_state.
                try:
                    await self._execute_save_funnel_context({
                        "job_id": job_id,
                        "service": "novomcp-qm",
                        "context": {
                            "tool": "run_conformer_search",
                            "smiles": smiles,
                            "max_conformers": max_conformers,
                            "method": method,
                        }
                    }, context=context)
                except Exception:
                    pass  # Non-fatal

                return ToolResult(
                    success=True,
                    data={
                        "job_id": job_id,
                        "status": "submitted",
                        "smiles": smiles,
                        "max_conformers": max_conformers,
                        "method": method,
                        "message": (
                            f"Conformer search submitted for {smiles[:40]}. "
                            f"Method: {method}, max conformers: {max_conformers}. "
                            f"CREST typically takes 5-15 minutes. "
                            f"IMPORTANT: Tell the user to wait ~10 minutes, then ask them to say "
                            f"'check job {job_id}' so you can poll. Do NOT auto-poll in a loop — "
                            f"you will exhaust your tool call budget. Poll at most 2 times per turn. "
                            f"Progress stays at 10% during computation — this is normal, not a stall."
                        ),
                        "tool_suggestions": ["get_job_status"],
                    },
                    usage={"tool": "run_conformer_search"},
                )

            # Synchronous response (fallback when Redis unavailable)
            conf_summary = []
            for c in data.get("conformers", []):
                conf_summary.append({
                    "rank": c["rank"],
                    "energy_kcal_mol": c["energy_kcal_mol"],
                    "boltzmann_population": c["population"],
                })

            return ToolResult(
                success=True,
                data={
                    "smiles": data["smiles"],
                    "n_conformers": data["n_conformers"],
                    "energy_range_kcal": data.get("energy_range_kcal"),
                    "method": data["method"],
                    "wall_time_seconds": data.get("wall_time_seconds"),
                    "conformers": conf_summary,
                },
                usage={"tool": "run_conformer_search"},
            )
        except Exception as e:
            logger.exception(f"Conformer search error for {smiles}: {e}")
            return ToolResult(success=False, error=f"Conformer search failed: {str(e)}")

    async def _execute_dock_with_strain(self, args: Dict[str, Any]) -> ToolResult:
        """Calculate strain energy for a docked pose."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        try:
            response = await self._call_service(
                "novomcp-qm",
                "/api/strain-energy",
                {
                    "smiles": smiles,
                    "docked_xyz": args.get("docked_xyz"),
                },
                timeout=120.0,
            )

            if response.status_code != 200:
                detail = response.text[:300]
                return ToolResult(success=False, error=f"Strain calculation failed ({response.status_code}): {detail}")

            data = response.json()
            return ToolResult(
                success=True,
                data={
                    "smiles": data["smiles"],
                    "strain_energy_kcal_mol": data["strain_energy_kcal_mol"],
                    "interpretation": data["interpretation"],
                    "method": data["method"],
                    "wall_time_seconds": data.get("wall_time_seconds"),
                },
                usage={"tool": "dock_with_strain"},
            )
        except Exception as e:
            logger.exception(f"Strain energy error for {smiles}: {e}")
            return ToolResult(success=False, error=f"Strain calculation failed: {str(e)}")

    # =========================================================================
    # Neural Network Potentials via novomcp-nnp
    # =========================================================================

    async def _execute_search_materials_project(self, args: Dict[str, Any]) -> ToolResult:
        """Search Materials Project database for inorganic materials."""
        query = args.get("query")
        if not query:
            return ToolResult(success=False, error="Missing required parameter: query")

        search_type = args.get("search_type", "formula")
        top_k = min(args.get("top_k", 5), 20)

        mp_api_key = os.getenv("MP_API_KEY", "")
        if not mp_api_key:
            return ToolResult(
                success=False,
                error="Materials Project API key not configured. Set MP_API_KEY environment variable."
            )

        try:
            base_url = "https://api.materialsproject.org/materials/summary"
            headers = {"X-API-KEY": mp_api_key, "accept": "application/json"}

            # Build query parameters based on search type
            params = {"_limit": top_k, "_fields": ",".join([
                "material_id", "formula_pretty", "composition",
                "band_gap", "is_metal", "is_stable",
                "energy_above_hull", "formation_energy_per_atom",
                "density", "nsites", "symmetry",
                "theoretical",
            ])}

            if search_type == "material_id":
                # Direct lookup by material ID — MP API v3 uses query param, not path
                params["material_ids"] = query
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    response = await client.get(base_url, headers=headers, params=params)
                    if response.status_code == 404:
                        return ToolResult(success=True, data={
                            "query": query, "search_type": search_type,
                            "count": 0, "results": [],
                            "message": f"Material ID '{query}' not found in Materials Project"
                        })
                    response.raise_for_status()
                    data = response.json()
                    materials = data.get("data", [])

            elif search_type == "chemsys":
                params["chemsys"] = query
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    response = await client.get(base_url, headers=headers, params=params)
                    response.raise_for_status()
                    data = response.json()
                    materials = data.get("data", [])

            else:  # formula
                params["formula"] = query
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    response = await client.get(base_url, headers=headers, params=params)
                    response.raise_for_status()
                    data = response.json()
                    materials = data.get("data", [])

            # Format results
            results = []
            for mat in materials[:top_k]:
                entry = {
                    "material_id": mat.get("material_id"),
                    "formula": mat.get("formula_pretty"),
                    "band_gap_ev": mat.get("band_gap"),
                    "is_metal": mat.get("is_metal"),
                    "is_stable": mat.get("is_stable"),
                    "energy_above_hull_ev_atom": mat.get("energy_above_hull"),
                    "formation_energy_ev_atom": mat.get("formation_energy_per_atom"),
                    "density_g_cm3": mat.get("density"),
                    "n_sites": mat.get("nsites"),
                    "theoretical": mat.get("theoretical"),
                }
                sym = mat.get("symmetry") or {}
                if sym:
                    entry["crystal_system"] = sym.get("crystal_system")
                    entry["space_group"] = sym.get("symbol")
                results.append(entry)

            return ToolResult(
                success=True,
                data={
                    "query": query,
                    "search_type": search_type,
                    "count": len(results),
                    "results": results,
                    "source": "Materials Project (materialsproject.org)",
                    "note": "Band gaps computed with PBE DFT — systematically underestimated by ~2 eV vs experiment for semiconductors.",
                },
                usage={"tool": "search_materials_project"},
            )

        except httpx.HTTPStatusError as e:
            return ToolResult(
                success=False,
                error=f"Materials Project API error ({e.response.status_code}): {e.response.text[:200]}"
            )
        except Exception as e:
            logger.exception(f"Materials Project search error: {e}")
            return ToolResult(success=False, error=f"Materials Project search failed: {str(e)}")

    async def _execute_compute_energy(self, args: Dict[str, Any]) -> ToolResult:
        """Compute molecular energy using neural network potentials."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        method = args.get("method") or args.get("model") or "auto"

        try:
            response = await self._call_service(
                "novomcp-nnp",
                "/api/compute-energy",
                {"smiles": smiles, "method": method},
                timeout=30.0,
            )

            if response.status_code != 200:
                detail = response.text[:300]
                return ToolResult(success=False, error=f"NNP computation failed ({response.status_code}): {detail}")

            data = response.json()
            return ToolResult(
                success=True,
                data={
                    "smiles": data["smiles"],
                    "energy_ev": data["energy_ev"],
                    "energy_kcal_mol": data["energy_kcal_mol"],
                    "forces_max_ev_ang": data.get("forces_max_ev_ang"),
                    "forces_rms_ev_ang": data.get("forces_rms_ev_ang"),
                    "method": data["method"],
                    "n_atoms": data["n_atoms"],
                    "wall_time_ms": data.get("wall_time_ms"),
                },
                usage={"tool": "compute_energy"},
            )
        except Exception as e:
            logger.exception(f"NNP energy error for {smiles}: {e}")
            return ToolResult(success=False, error=f"NNP computation failed: {str(e)}")

    async def _execute_optimize_geometry_nnp(self, args: Dict[str, Any]) -> ToolResult:
        """Optimize geometry using neural network potentials (ASE BFGS)."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        payload: Dict[str, Any] = {"smiles": smiles}
        if args.get("method"):
            payload["method"] = args["method"]
        if args.get("fmax") is not None:
            payload["fmax"] = args["fmax"]
        if args.get("charge") is not None:
            payload["charge"] = args["charge"]
        if args.get("uhf") is not None:
            payload["uhf"] = args["uhf"]

        try:
            response = await self._call_service(
                "novomcp-nnp",
                "/api/optimize-geometry",
                payload,
                timeout=60.0,
            )

            if response.status_code != 200:
                detail = response.text[:300]
                return ToolResult(success=False, error=f"NNP optimization failed ({response.status_code}): {detail}")

            data = response.json()
            return ToolResult(success=True, data=data, usage={"tool": "optimize_geometry_nnp"})
        except Exception as e:
            logger.exception(f"NNP optimization error for {smiles}: {e}")
            return ToolResult(success=False, error=f"NNP optimization failed: {str(e)}")

    # =========================================================================
    # Property Prediction (pKa, Solubility, BDE) via novomcp-properties
    # =========================================================================

    async def _execute_predict_pka(self, args: Dict[str, Any]) -> ToolResult:
        """Predict pKa for a molecule using novomcp-properties service."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        try:
            response = await self._call_service(
                "novomcp-properties",
                "/api/predict-pka",
                {"smiles": smiles},
                timeout=30.0,
            )

            if response.status_code != 200:
                detail = response.text[:200]
                return ToolResult(success=False, error=f"pKa prediction failed ({response.status_code}): {detail}")

            data = response.json()
            return ToolResult(
                success=True,
                data={
                    "smiles": data["smiles"],
                    "pka_values": data["pka_values"],
                    "ionizable_groups": data["ionizable_groups"],
                    "method": data["method"],
                    "confidence": data.get("confidence"),
                    "interpretation": self._interpret_pka(data["pka_values"], data["ionizable_groups"]),
                },
                usage={"tool": "predict_pka"},
            )
        except Exception as e:
            logger.exception(f"pKa prediction error for {smiles}: {e}")
            return ToolResult(success=False, error=f"pKa prediction failed: {str(e)}")

    def _interpret_pka(self, pka_values: list, groups: list) -> str:
        """Generate a brief interpretation of pKa results."""
        if not pka_values or groups == ["none_detected"]:
            return "No ionizable groups detected. Molecule expected to be neutral across physiological pH range."

        parts = []
        for val in pka_values:
            if val < 4:
                parts.append(f"pKa {val}: strong acid, fully ionized at physiological pH (7.4)")
            elif val < 7:
                parts.append(f"pKa {val}: weak acid, partially ionized at physiological pH")
            elif val < 9:
                parts.append(f"pKa {val}: near neutral at physiological pH")
            else:
                parts.append(f"pKa {val}: basic group, protonated at physiological pH")

        return "; ".join(parts)

    async def _execute_predict_solubility(self, args: Dict[str, Any]) -> ToolResult:
        """Predict aqueous solubility using novomcp-properties service."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        temperature_k = args.get("temperature_k", 298.15)
        if temperature_k < 273 or temperature_k > 373:
            return ToolResult(success=False, error="Temperature must be between 273K and 373K")

        try:
            response = await self._call_service(
                "novomcp-properties",
                "/api/predict-solubility",
                {"smiles": smiles, "temperature_k": temperature_k},
                timeout=30.0,
            )

            if response.status_code != 200:
                detail = response.text[:200]
                return ToolResult(success=False, error=f"Solubility prediction failed ({response.status_code}): {detail}")

            data = response.json()
            temp_c = round(temperature_k - 273.15, 1)
            return ToolResult(
                success=True,
                data={
                    "smiles": data["smiles"],
                    "logS": data["logs"],
                    "solubility_mg_ml": data.get("solubility_mg_ml"),
                    "temperature": f"{temp_c}C ({temperature_k}K)",
                    "category": data["category"],
                    "method": data["method"],
                    "confidence": data.get("confidence"),
                },
                usage={"tool": "predict_solubility"},
            )
        except Exception as e:
            logger.exception(f"Solubility prediction error for {smiles}: {e}")
            return ToolResult(success=False, error=f"Solubility prediction failed: {str(e)}")

    async def _execute_predict_bde(self, args: Dict[str, Any]) -> ToolResult:
        """Predict bond dissociation energies using novomcp-properties service."""
        smiles = args.get("smiles")
        if not smiles:
            return ToolResult(success=False, error="Missing required parameter: smiles")

        try:
            response = await self._call_service(
                "novomcp-properties",
                "/api/predict-bde",
                {"smiles": smiles},
                timeout=30.0,
            )

            if response.status_code != 200:
                detail = response.text[:200]
                return ToolResult(success=False, error=f"BDE prediction failed ({response.status_code}): {detail}")

            data = response.json()
            weakest = data.get("weakest_bond")
            interpretation = ""
            if weakest and weakest.get("bde_kcal_mol") is not None:
                bde_val = weakest["bde_kcal_mol"]
                if bde_val < 85:
                    interpretation = f"Weakest bond ({bde_val} kcal/mol) is highly susceptible to metabolic oxidation by CYP enzymes. Likely metabolic soft spot."
                elif bde_val < 95:
                    interpretation = f"Weakest bond ({bde_val} kcal/mol) has moderate susceptibility to hydrogen abstraction."
                else:
                    interpretation = f"Weakest bond ({bde_val} kcal/mol) is relatively stable. No obvious metabolic soft spots."

            return ToolResult(
                success=True,
                data={
                    "smiles": data["smiles"],
                    "bonds": data["bonds"],
                    "weakest_bond": weakest,
                    "method": data["method"],
                    "interpretation": interpretation,
                    "bond_count": len(data["bonds"]),
                },
                usage={"tool": "predict_bde"},
            )
        except Exception as e:
            logger.exception(f"BDE prediction error for {smiles}: {e}")
            return ToolResult(success=False, error=f"BDE prediction failed: {str(e)}")

    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
