"""
NovoMCP Developability Report — REST-only flagship endpoint.

POST /v1/developability-report

This endpoint is the engine's REST-only flagship surface. It takes a
perturbation result (compound SMILES + observed cellular effect from a screen)
and returns a structured developability-signal report assembled by calling the
existing chemistry chain:

    NovoExpert-2 ADMET (addie-models)
    FAVES V4 compliance (faves-compliance)
    NovoExpert-3 clinical clearance (addie-models / novoexpert)
    + optional docking (autodock-gpu) when target_pdb supplied

Locked posture (see CLAUDE.md, the brief, and the moat plan §7):

  - **NOT an MCP tool.** Top-level /v1 path, NOT under /v1/tools/{name}.
  - **Informational, NOT adjudicative.** No decision/verdict/recommendation
    field anywhere in the response.
  - **Mode A v1 only.** Direct chemical perturbagen → chemistry signals.
  - **Open-source-where-viable.** Each chain link carries provenance with a
    nullable `open_source_alternative_validated` flag.
  - **Docking on-request only.** Default is off; include_docking=true +
    target_pdb required to populate the `docking` block.

Reuses, not reimplements:

  - `MCPToolExecutor._execute_predict_admet`
  - `MCPToolExecutor._execute_check_compliance`
  - `MCPToolExecutor._execute_predict_clinical_outcomes`
  - `MCPToolExecutor._execute_dock_molecules` (when requested)

…all reached through the singleton `_tool_executor` set up in
`mcp.router.setup_mcp`.

Audit trail is written to dashboard-aggregator's `funnel_audit_log` via the
same `/api/v1/funnel/{funnel_id}/log` path the MCP `_autolog_event` uses, but
tagged `surface="rest-v1-developability-report"` and synthetic
`tool_name="v1_developability_report"`. Concurrency-capped via the same
`_AUTOLOG_SEMAPHORE` to avoid starving the aggregator's pymssql pool.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mcp.auth import MCPUser
from mcp.router import get_mcp_user
from mcp import router as mcp_router_module
from mcp import tools as mcp_tools_module
# Reused from mcp/tools.py to keep the REST surface aligned with the existing
# MCP-path NaN/Inf handling. Standard library json.dumps raises ValueError on
# out-of-range floats, which FastAPI surfaces as 500. Several upstream
# services (NovoExpert-3 SHAP arrays in particular) can legitimately produce
# NaN for compounds whose feature vector has missing values; the sanitizer
# replaces those with None so the report stays parseable.
from mcp.tools import _sanitize_for_json
from models.developability_report import (
    DEVELOPABILITY_REPORT_SCHEMA_VERSION,
    AdmetLiability,
    ChainStageStatus,
    ClinicalClearanceSignal,
    CommodityContext,
    CompetenceFlag,
    ConfidenceLabel,
    DevelopabilityReport,
    DevelopabilityReportRequest,
    DockingBlock,
    DockingPose,
    FavesCompliance,
    FavesOverallStatus,
    Provenance,
    ShapFeature,
    StructuralAlert,
    confidence_label_for,
)

logger = logging.getLogger(__name__)


# Two routes share this module:
#   /v1/tools/developability_report  — canonical, catalog-shape envelope per the
#                                       2026-06-15 design unification ("69 MCP
#                                       tools, 2 API-only tools, one call
#                                       pattern"). Same URL pattern, same
#                                       `{"arguments": {...}}` body, same
#                                       `{"result": ..., "usage": ...}` response
#                                       as every /v1/tools/{name} catalog tool;
#                                       the only difference is this entry isn't
#                                       in the MCP server (x-mcp-exposed=false
#                                       in the OpenAPI spec).
#   /v1/developability-report        — deprecated alias retained for backwards
#                                       compatibility with shipped clients
#                                       (T2-D harness, demo script). Returns the
#                                       legacy flat shape. Removed once the
#                                       deprecation grace window closes.
router = APIRouter(tags=["NovoMCP v1 — Developability Report"])


# ---------------------------------------------------------------------------
# Competence allow-list — non-negotiable per NovoExpert-3 documented perf
# ---------------------------------------------------------------------------

_IN_DOMAIN_AREAS = {"cardiovascular", "gastrointestinal", "mainstream"}
_OUT_OF_DOMAIN_AREAS = {"oncology", "cns", "infectious"}

# Synthetic surface tag — namespaces this route's funnel_id slot away from the
# MCP per-conversation slots. Dashboard-aggregator persists this verbatim into
# funnel_audit_log.surface so analytics can slice the REST flagship traffic.
_SURFACE_TAG = "rest-v1-developability-report"
_SYNTHETIC_TOOL_NAME = "v1_developability_report"

# Borderline-blob snapshot threshold. Placeholder for Harrison — when any
# chain stage's confidence falls within this band, the assembly path stores a
# pointer to a full-report JSON blob alongside the audit row. The actual blob
# write is left as a follow-up (no blob plumbing yet); for now we just stamp
# the `_borderline_snapshot_pending=true` flag on the audit row so the future
# blob-snapshotter can pick it up.
_BORDERLINE_MIN = float(os.getenv("DEVELOPABILITY_REPORT_BORDERLINE_MIN", "0.35"))
_BORDERLINE_MAX = float(os.getenv("DEVELOPABILITY_REPORT_BORDERLINE_MAX", "0.65"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _hash_request(req: DevelopabilityReportRequest) -> str:
    """SHA256 of the canonical request JSON. Stored on the audit row as the
    deduplication key for replay attacks and idempotency analytics."""
    canon = json.dumps(req.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _mint_funnel_id(user_id: Optional[str]) -> str:
    """Synthetic funnel_id for unanchored REST calls.

    Format: `devrep_<epoch_s>_<user_hash6>_<rand4>` — collision-resistant for
    parallel calls under the same key. The user portion is hashed (not raw) so
    funnel_ids are not user-enumerable if the audit row is ever leaked.
    """
    user_part = "anon"
    if user_id:
        user_part = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:6]
    epoch_s = int(time.time())
    rand = uuid.uuid4().hex[:4]
    return f"devrep_{epoch_s}_{user_part}_{rand}"


def _classify_competence(therapeutic_area: str) -> CompetenceFlag:
    """Map a therapeutic_area string into a CompetenceFlag.

    Allow-list match is case-insensitive on the canonical name. Anything not
    in the allow-list is out-of-domain; this is conservative on purpose —
    NovoExpert-3's documented bimodal performance is the source of truth and
    any new area must be explicitly added once validated.
    """
    canon = (therapeutic_area or "").strip().lower()
    if canon in _IN_DOMAIN_AREAS:
        return CompetenceFlag(
            in_domain=True,
            therapeutic_area=canon,
            reason=(
                f"'{canon}' is in NovoExpert-3's validated allow-list "
                "(CV 0.76 / GI 0.80 / mainstream 0.72 AUROC). Clinical "
                "clearance signal will be surfaced."
            ),
        )
    if canon in _OUT_OF_DOMAIN_AREAS:
        return CompetenceFlag(
            in_domain=False,
            therapeutic_area=canon,
            reason=(
                f"'{canon}' is outside NovoExpert-3's validated domain "
                "(documented bimodal performance: oncology 0.47 / CNS 0.48 / "
                "infectious 0.36 AUROC). Clinical clearance signal "
                "suppressed; caller must source clinical confidence elsewhere."
            ),
        )
    return CompetenceFlag(
        in_domain=False,
        therapeutic_area=canon or "unknown",
        reason=(
            "Therapeutic area is unknown or not on the validated allow-list. "
            "Clinical clearance signal suppressed."
        ),
    )


def _normalize_perturbation_input(req: DevelopabilityReportRequest) -> Dict[str, Any]:
    """Map the discriminated-union payload into a flat chain-input dict.

    Both `generic` and `lincs` reduce to {smiles, phenotype, cell_context,
    desired_effect} for Mode A v1. The original screen_format is preserved on
    the `_original` key so the audit row keeps the raw shape.
    """
    p = req.perturbation
    smiles = p.smiles
    phenotype: Optional[str] = None
    cell_context: Optional[str] = None
    desired_effect: Optional[str] = getattr(p, "desired_effect", None)
    original: Dict[str, Any] = p.model_dump(mode="json")

    if p.screen_format == "generic":
        phenotype = p.phenotype
        cell_context = p.cell_context
    elif p.screen_format == "lincs":
        # LINCS doesn't carry a free-text phenotype — synthesize a stable
        # one-line phenotype string for audit-row readability. The signature
        # genes ride along on the audit row, not into the chemistry chain
        # (that's Mode B/C territory, out of scope here).
        up = ",".join(p.signature_top_up[:5])
        down = ",".join(p.signature_top_down[:5])
        phenotype = (
            f"LINCS L1000 signature: up=[{up}] down=[{down}]"
            + (f" pert_id={p.pert_id}" if p.pert_id else "")
        )
        cell_context = p.cell_id

    return {
        "smiles": smiles,
        "phenotype": phenotype,
        "cell_context": cell_context,
        "desired_effect": desired_effect,
        "_original": original,
    }


def _structural_alerts_from_check_compliance(payload: Dict[str, Any]) -> List[StructuralAlert]:
    """Pull alert dicts out of the check_compliance response.

    `check_compliance` returns `base_compliance` and `context_compliance` with
    nested alerts; we look in the typical locations and pass each through
    StructuralAlert (which uses extra="allow" so unknown fields ride along).
    """
    alerts: List[StructuralAlert] = []
    candidates: List[Dict[str, Any]] = []

    for container_key in ("base_compliance", "context_compliance"):
        container = payload.get(container_key) or {}
        if not isinstance(container, dict):
            continue
        for alerts_key in ("structural_alerts", "alerts", "matched_alerts"):
            raw = container.get(alerts_key) or []
            if isinstance(raw, list):
                candidates.extend(a for a in raw if isinstance(a, dict))

    for a in candidates:
        try:
            alerts.append(
                StructuralAlert(
                    alert_id=str(a.get("alert_id") or a.get("id") or a.get("name") or "unknown"),
                    alert_name=str(a.get("alert_name") or a.get("name") or "unnamed alert"),
                    severity=a.get("severity"),
                    matched_smarts=a.get("smarts") or a.get("matched_smarts"),
                    notes=a.get("notes") or a.get("description"),
                )
            )
        except Exception as e:  # pragma: no cover — defensive
            logger.debug("skipping malformed alert dict: %s", e)
    return alerts


def _faves_status_from_string(s: Optional[str]) -> FavesOverallStatus:
    if not s:
        return FavesOverallStatus.UNKNOWN
    try:
        return FavesOverallStatus(s)
    except ValueError:
        return FavesOverallStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Provenance collection
# ---------------------------------------------------------------------------


def _collect_provenance(
    *,
    stage: str,
    source_service: str,
    model_name: str,
    called_at_ms: int,
    latency_ms: int,
    downstream_payload: Dict[str, Any],
    degraded: bool = False,
    degraded_reason: Optional[str] = None,
) -> Provenance:
    """Build a Provenance block from a downstream service's response payload.

    Each downstream service formats version metadata slightly differently:
      - addie-models /addie/process returns version on the batch envelope; the
        executor (_execute_predict_admet at tools.py:8520+) does not currently
        surface it. We probe well-known keys; default to "unknown" rather than
        fabricate.
      - faves-compliance returns `faves_v3` / `faves_v4` block with a version
        nested under `version` or `model_version`.
      - novoexpert returns `model_version` and `model_name` at the top level
        (see _execute_predict_clinical_outcomes at tools.py:8792-8793).
    """
    payload = downstream_payload or {}

    version = (
        payload.get("model_version")
        or payload.get("version")
        or (payload.get("meta") or {}).get("model_version")
        or "unknown"
    )
    snapshot = (
        payload.get("training_data_snapshot_hash")
        or (payload.get("meta") or {}).get("training_data_snapshot_hash")
    )
    model_card = (
        payload.get("model_card_url")
        or (payload.get("meta") or {}).get("model_card_url")
    )
    confidence = payload.get("confidence")
    if confidence is None:
        confidence = (payload.get("meta") or {}).get("confidence")

    # Confidence rules:
    #   - upstream-provided numeric (if present) wins
    #   - degraded stage → None (excluded from rollup by _compute_overall_confidence)
    #   - non-degraded stage with no upstream-provided confidence → 0.85 default
    #     baseline. This represents "the chain ran cleanly against this stage's
    #     upstream and a populated signal came back" — which is meaningful
    #     non-trivial confidence even when the upstream doesn't emit a numeric
    #     value. Without this default, overall_confidence (mean across stages)
    #     drops to 0.0 whenever no upstream provides a numeric — misreading the
    #     FAVES posture as "very_low" confidence on otherwise clean chains.
    #     0.85 was chosen as the floor for "ok": high enough to read as a real
    #     positive signal, low enough not to mask the real cross-stage spread
    #     when upstream-provided numerics are present on some stages.
    if not isinstance(confidence, (int, float)):
        confidence = None if degraded else 0.85

    return Provenance(
        model_name=model_name,
        model_version=str(version),
        source_service=source_service,
        called_at_ms=called_at_ms,
        latency_ms=latency_ms,
        confidence=confidence,
        training_data_snapshot_hash=snapshot,
        model_card_url=model_card,
        open_source_alternative_validated=None,  # Set per-stage when known
        degraded=degraded,
        degraded_reason=degraded_reason,
    )


# ---------------------------------------------------------------------------
# Chain execution — calls existing executors. Does NOT reimplement chemistry.
# ---------------------------------------------------------------------------


async def _run_chain(
    *,
    req: DevelopabilityReportRequest,
    normalized: Dict[str, Any],
    competence: CompetenceFlag,
) -> Tuple[
    Optional[AdmetLiability],
    Optional[FavesCompliance],
    Optional[ClinicalClearanceSignal],
    Optional[CommodityContext],
    List[ChainStageStatus],
]:
    """Run the three core chain stages in parallel via the singleton executor.

    Returns (admet, faves, clinical, commodity, chain_status_list).
    """
    executor = mcp_router_module._tool_executor
    if executor is None:
        # Should never happen in a properly-initialized service. We raise an
        # HTTP 503 here rather than silently degrade because the entire
        # report depends on the executor being up.
        raise HTTPException(status_code=503, detail="MCP tool executor not initialized")

    smiles = req.smiles
    chain_trail: List[ChainStageStatus] = []

    # Stage 1 — ADMET (addie-models)
    admet_started = _now_ms()
    admet_task = executor._execute_predict_admet({"smiles": smiles})

    # Stage 2 — FAVES (faves-compliance) via _execute_check_compliance
    faves_started = _now_ms()
    faves_task = executor._execute_check_compliance({
        "smiles": smiles,
        "context": {
            "intended_use": req.intended_use,
            "jurisdiction": req.jurisdiction,
            "therapeutic_area": req.therapeutic_area,
        },
    })

    # Stage 3 — clinical clearance (novoexpert) — only when in-domain
    clinical_task = None
    clinical_started = None
    if competence.in_domain:
        clinical_started = _now_ms()
        clinical_task = executor._execute_predict_clinical_outcomes({
            "smiles": smiles,
            "therapeutic_area": competence.therapeutic_area,
        })

    # Gather in parallel; clinical is awaited as a no-op when suppressed
    tasks = [admet_task, faves_task]
    if clinical_task is not None:
        tasks.append(clinical_task)

    results = await asyncio.gather(*tasks, return_exceptions=True)
    admet_result = results[0]
    faves_result = results[1]
    clinical_result = results[2] if clinical_task is not None else None

    # ---- ADMET block ----
    admet_block: Optional[AdmetLiability] = None
    admet_latency = _now_ms() - admet_started
    if _is_ok(admet_result):
        data = admet_result.data or {}
        tox = data.get("toxicity") or {}
        meta = data.get("metabolism") or {}
        raw = data.get("raw_predictions") or {}
        # Cardiotox now comes from the validated DICTrank head (cardiotoxicity_dict);
        # a prior release retired the legacy cardiotoxicity_max. Fall back to the old
        # key for any cached/older upstream response. Populate BOTH the new canonical
        # field and the deprecated alias (back-compat, schema 1.1.0).
        cardiotox = tox.get("cardiotoxicity_dict", tox.get("cardiotoxicity_max"))
        admet_block = AdmetLiability(
            cyp_inhibition_risk_score=raw.get("cyp_inhibition_risk_score"),
            cyp_substrate_max_probability=raw.get("cyp_substrate_max_probability"),
            hepatotoxicity_probability=tox.get("hepatotoxicity"),
            cardiotoxicity_dict_probability=cardiotox,
            cardiotoxicity_max_probability=cardiotox,  # deprecated alias, back-compat
            herg_blocker_probability=raw.get("herg_blocker_probability"),
            ames_mutagenicity_probability=tox.get("ames_mutagenicity"),
            overall_toxicity_score=tox.get("overall_toxicity_score"),
            raw_categories={
                k: v for k, v in data.items()
                if k in ("absorption", "distribution", "metabolism", "excretion",
                          "toxicity", "nuclear_receptors", "stress_response", "properties")
                and isinstance(v, dict) and v
            } if req.include_raw_categories else None,
            provenance=_collect_provenance(
                stage="admet",
                source_service="addie-models",
                model_name="NovoExpert-2-ADMET",
                called_at_ms=admet_started,
                latency_ms=admet_latency,
                downstream_payload=raw if isinstance(raw, dict) else data,
            ),
        )
        chain_trail.append(ChainStageStatus(stage="admet", status="ok"))
    else:
        reason = _failure_reason(admet_result)
        admet_block = AdmetLiability(
            provenance=_collect_provenance(
                stage="admet",
                source_service="addie-models",
                model_name="NovoExpert-2-ADMET",
                called_at_ms=admet_started,
                latency_ms=admet_latency,
                downstream_payload={},
                degraded=True,
                degraded_reason=reason,
            ),
        )
        chain_trail.append(ChainStageStatus(stage="admet", status="degraded", reason=reason))

    # ---- FAVES block + commodity context ----
    faves_block: Optional[FavesCompliance] = None
    commodity: Optional[CommodityContext] = None
    faves_latency = _now_ms() - faves_started
    if _is_ok(faves_result):
        data = faves_result.data or {}
        ctx = data.get("context_compliance") or {}
        base = data.get("base_compliance") or {}
        faves_block = FavesCompliance(
            overall_status=_faves_status_from_string(data.get("overall_status")),
            raw_overall_status=data.get("raw_overall_status"),
            structural_alerts=_structural_alerts_from_check_compliance(data),
            has_pains=base.get("has_pains"),
            is_aggregator_risk=base.get("is_aggregator_risk"),
            boiled_egg_class=base.get("boiled_egg_class"),
            regulatory_pathway=data.get("regulatory_pathway"),
            jurisdiction=req.jurisdiction,
            intended_use=req.intended_use,
            provenance=_collect_provenance(
                stage="faves",
                source_service="faves-compliance",
                model_name="FAVES-V4",
                called_at_ms=faves_started,
                latency_ms=faves_latency,
                downstream_payload=ctx or base,
            ),
        )
        # Reflective commodity context, derived from base_compliance flags
        commodity = CommodityContext(
            is_known_drug=bool(base.get("is_known_drug")) if base.get("is_known_drug") is not None else None,
            is_fda_whitelisted=bool(base.get("is_whitelisted")) if base.get("is_whitelisted") is not None else None,
            chembl_match_count=base.get("chembl_match_count"),
            pubchem_cid=base.get("pubchem_cid"),
            commodity_score=base.get("commodity_score"),
            notes=base.get("commodity_notes"),
        )
        chain_trail.append(ChainStageStatus(stage="faves", status="ok"))
        chain_trail.append(ChainStageStatus(stage="commodity_context", status="ok"))
    else:
        reason = _failure_reason(faves_result)
        faves_block = FavesCompliance(
            overall_status=FavesOverallStatus.UNKNOWN,
            structural_alerts=[],
            jurisdiction=req.jurisdiction,
            intended_use=req.intended_use,
            provenance=_collect_provenance(
                stage="faves",
                source_service="faves-compliance",
                model_name="FAVES-V4",
                called_at_ms=faves_started,
                latency_ms=faves_latency,
                downstream_payload={},
                degraded=True,
                degraded_reason=reason,
            ),
        )
        chain_trail.append(ChainStageStatus(stage="faves", status="degraded", reason=reason))
        chain_trail.append(ChainStageStatus(stage="commodity_context", status="degraded", reason=reason))

    # ---- Clinical clearance block ----
    clinical_block: Optional[ClinicalClearanceSignal] = None
    if clinical_task is None:
        chain_trail.append(ChainStageStatus(
            stage="clinical_clearance",
            status="suppressed",
            reason=competence.reason,
        ))
    else:
        clinical_latency = _now_ms() - (clinical_started or _now_ms())
        if _is_ok(clinical_result):
            data = clinical_result.data or {}
            shap_feats: List[ShapFeature] = []
            for i, feat in enumerate(data.get("top_shap_features") or []):
                if not isinstance(feat, dict):
                    continue
                try:
                    shap_feats.append(ShapFeature(
                        feature_name=str(feat.get("feature_name") or feat.get("feature") or f"f{i}"),
                        contribution=float(feat.get("contribution") or feat.get("value") or 0.0),
                        rank=int(feat.get("rank") or (i + 1)),
                    ))
                except Exception as e:  # pragma: no cover
                    logger.debug("skipping malformed shap entry: %s", e)
            clinical_block = ClinicalClearanceSignal(
                phase1_clearance_probability=data.get("phase1_clearance_probability"),
                phase1_clearance_probability_raw=data.get("phase1_clearance_probability_raw"),
                calibration=data.get("calibration"),
                feature_count=data.get("feature_count"),
                missing_features=data.get("missing_features") or [],
                top_shap_features=shap_feats,
                feature_sources=data.get("feature_sources") or {},
                provenance=_collect_provenance(
                    stage="clinical_clearance",
                    source_service="novoexpert",
                    model_name=str(data.get("model_name") or "NovoExpert-3"),
                    called_at_ms=clinical_started,
                    latency_ms=clinical_latency,
                    downstream_payload=data,
                ),
            )
            chain_trail.append(ChainStageStatus(stage="clinical_clearance", status="ok"))
        else:
            # In-domain but failed downstream — leave clinical_block=None and
            # log a degraded chain entry. The engine never surfaces a
            # low-confidence clinical number, even on in-domain stage failure.
            reason = _failure_reason(clinical_result)
            chain_trail.append(ChainStageStatus(
                stage="clinical_clearance",
                status="degraded",
                reason=reason,
            ))

    return admet_block, faves_block, clinical_block, commodity, chain_trail


async def _run_docking(
    *,
    req: DevelopabilityReportRequest,
    chain_trail: List[ChainStageStatus],
) -> Optional[DockingBlock]:
    """Optional Stage 4 — docking.

    Per Harrison's locked default: docking is NEVER default. Only runs when:
      - include_docking=true on the request, AND
      - target_pdb is supplied on the request.

    Returns None otherwise, and adds a `skipped` chain trail entry. When
    docking succeeds we populate a DockingBlock; when it fails we append a
    `degraded` chain trail entry and still return None (no half-populated
    block in the response).
    """
    if not req.include_docking or not req.target_pdb:
        chain_trail.append(ChainStageStatus(
            stage="docking",
            status="skipped",
            reason="include_docking=false or target_pdb not supplied",
        ))
        return None

    executor = mcp_router_module._tool_executor
    if executor is None:
        chain_trail.append(ChainStageStatus(
            stage="docking",
            status="degraded",
            reason="MCP tool executor not initialized",
        ))
        return None

    started = _now_ms()
    try:
        dock_result = await executor._execute_dock_molecules({
            "smiles_list": [req.smiles],
            "protein_pdb_id": req.target_pdb,
        })
    except Exception as e:  # pragma: no cover — defensive
        logger.warning("docking dispatch failed: %s", e)
        chain_trail.append(ChainStageStatus(
            stage="docking",
            status="degraded",
            reason=f"dispatch error: {str(e)[:200]}",
        ))
        return None

    latency = _now_ms() - started
    if not _is_ok(dock_result):
        chain_trail.append(ChainStageStatus(
            stage="docking",
            status="degraded",
            reason=_failure_reason(dock_result),
        ))
        return None

    data = dock_result.data or {}
    poses_raw = data.get("poses") or data.get("results") or []
    poses: List[DockingPose] = []
    best: Optional[float] = None
    for i, p in enumerate(poses_raw):
        if not isinstance(p, dict):
            continue
        affinity = p.get("binding_affinity_kcal_mol") or p.get("affinity") or p.get("score")
        if affinity is None:
            continue
        try:
            affinity_f = float(affinity)
        except (TypeError, ValueError):
            continue
        if best is None or affinity_f < best:
            best = affinity_f
        poses.append(DockingPose(
            rank=int(p.get("rank") or (i + 1)),
            binding_affinity_kcal_mol=affinity_f,
            rmsd_lb=p.get("rmsd_lb"),
            rmsd_ub=p.get("rmsd_ub"),
        ))

    chain_trail.append(ChainStageStatus(stage="docking", status="ok"))
    return DockingBlock(
        target_pdb=req.target_pdb,
        poses=poses,
        best_binding_affinity_kcal_mol=best,
        provenance=_collect_provenance(
            stage="docking",
            source_service="autodock-gpu",
            model_name="AutoDock-GPU",
            called_at_ms=started,
            latency_ms=latency,
            downstream_payload=data,
        ),
    )


def _is_ok(result: Any) -> bool:
    """True iff the result is a ToolResult with success=True. Handles
    `gather(..., return_exceptions=True)` outputs uniformly."""
    if result is None or isinstance(result, BaseException):
        return False
    return bool(getattr(result, "success", False))


def _failure_reason(result: Any) -> str:
    if isinstance(result, BaseException):
        return f"exception: {type(result).__name__}: {str(result)[:200]}"
    err = getattr(result, "error", None)
    if err:
        return str(err)[:300]
    return "unknown failure"


# ---------------------------------------------------------------------------
# Assembly — no decision logic, no thresholds, no classification
# ---------------------------------------------------------------------------


def _compute_overall_confidence(blocks: List[Optional[Any]]) -> float:
    """Mean of available stage confidences. NOT a developability score.

    This is *confidence in the signal*, not *should you advance*. The brief is
    explicit: no roll-up that implies a decision. Stages without confidence
    (e.g. FAVES, which is rules-based) are excluded from the mean.
    """
    confidences: List[float] = []
    for b in blocks:
        if b is None:
            continue
        prov = getattr(b, "provenance", None)
        if prov is None or prov.degraded:
            continue
        if prov.confidence is not None:
            confidences.append(float(prov.confidence))
    if not confidences:
        return 0.0
    return sum(confidences) / len(confidences)


def _assemble_report(
    *,
    req: DevelopabilityReportRequest,
    normalized: Dict[str, Any],
    competence: CompetenceFlag,
    admet: Optional[AdmetLiability],
    faves: Optional[FavesCompliance],
    clinical: Optional[ClinicalClearanceSignal],
    commodity: Optional[CommodityContext],
    docking: Optional[DockingBlock],
    chain_trail: List[ChainStageStatus],
    funnel_id: Optional[str],
) -> DevelopabilityReport:
    """Stitch the chain outputs into a DevelopabilityReport.

    NO decision logic, NO thresholds, NO classification beyond:
      - competence_flag (in/out of validated NovoExpert-3 domain)
      - clinical suppression when out-of-domain (sets clinical=None)

    The structure is the §7 contract. If you find yourself wanting to compute
    a "developability score" here that nudges the caller toward advance, stop:
    that's the caller's pipeline, not ours.
    """
    # Out-of-domain suppression: even if the stage somehow returned a value
    # (e.g. via a future retry path), force clinical to None to keep the
    # contract. This is the load-bearing competence gate.
    if not competence.in_domain:
        clinical = None

    model_versions: Dict[str, str] = {}
    for b in (admet, faves, clinical, docking):
        if b is None:
            continue
        prov = getattr(b, "provenance", None)
        if prov is None:
            continue
        # Stage key for the flat audit-row index: prefer source_service for
        # cross-call grep-ability ("which addie-models versions ran in May?").
        model_versions[prov.source_service] = prov.model_version

    confidence = _compute_overall_confidence([admet, faves, clinical, docking])
    label = confidence_label_for(confidence) or ConfidenceLabel.VERY_LOW

    # §7 honesty gate + T2-D spec row E5: when the competence flag fires
    # (OOD), overall_confidence must land in the refuse bin (very_low / low).
    # Without this cap the rollup averages ADMET + FAVES + commodity + docking
    # to their 0.85 baselines while clinical is suppressed to None -> composite
    # 0.85 -> very_high. The first LIVE T2-D verdict (2026-06-14) flagged
    # this on 54/54 OOD compounds; the chain returned confident labels despite
    # competence_flag.in_domain=false. Capping after the average preserves the
    # nested per-stage confidences (callers can still see ADMET confidence
    # 0.85 etc.) while making the rollup honest about the OOD posture.
    if not competence.in_domain:
        confidence = min(confidence, 0.15)
        label = ConfidenceLabel.VERY_LOW

    return DevelopabilityReport(
        schema_version=DEVELOPABILITY_REPORT_SCHEMA_VERSION,
        report_id=uuid.uuid4().hex,
        smiles=req.smiles,
        therapeutic_area=req.therapeutic_area,
        competence_flag=competence,
        overall_confidence=confidence,
        overall_confidence_label=label,
        admet_liability=admet,
        faves_compliance=faves,
        clinical_clearance_signal=clinical,
        commodity_context=commodity,
        docking=docking,
        chain=chain_trail,
        model_versions=model_versions,
        normalized_input=normalized,
        generated_at_ms=_now_ms(),
        funnel_id=funnel_id,
    )


def _is_borderline(report: DevelopabilityReport) -> bool:
    """True iff any chain stage's confidence falls in the borderline band.

    Triggers the blob-snapshot intent on the audit row. Threshold band is
    configurable via env vars; defaults to [0.35, 0.65]. Final threshold is
    Harrison's call — env vars give him a knob without redeploy.
    """
    for b in (report.admet_liability, report.faves_compliance,
              report.clinical_clearance_signal, report.docking):
        if b is None:
            continue
        prov = getattr(b, "provenance", None)
        if prov is None or prov.confidence is None:
            continue
        if _BORDERLINE_MIN <= prov.confidence <= _BORDERLINE_MAX:
            return True
    # Top-level confidence in the band also counts
    return _BORDERLINE_MIN <= report.overall_confidence <= _BORDERLINE_MAX


# ---------------------------------------------------------------------------
# Audit emission — REST-surface variant, parallel to MCP _autolog_event
# ---------------------------------------------------------------------------


async def _emit_audit(
    *,
    user: MCPUser,
    req: DevelopabilityReportRequest,
    report: DevelopabilityReport,
    request_hash: str,
) -> None:
    """Write the audit row to dashboard-aggregator.

    POSTs to the same `/api/v1/funnel/{funnel_id}/log` path as MCP autolog so
    funnel_audit_log row schema stays unified. Differences from MCP autolog:

      - surface = "rest-v1-developability-report"
      - tool_name = "v1_developability_report" (synthetic — there is no MCP
        tool of this name)
      - results_summary carries the structured fields callers care about:
        schema_version, model_versions, competence_flag, overall_confidence,
        chain trail summary, plus borderline-snapshot intent flag.

    Reuses `_AUTOLOG_SEMAPHORE` to share the 3-wide cap with MCP autolog —
    dashboard-aggregator's single shared pymssql connection is the
    bottleneck, and starving auth queries here would surface as 401s on
    other paths.
    """
    executor = mcp_router_module._tool_executor
    if executor is None:
        logger.warning("audit emission skipped: executor not initialized")
        return

    dashboard_url = getattr(executor, "dashboard_url", "")
    admin_key = getattr(executor, "dashboard_admin_key", "")
    if not dashboard_url:
        logger.warning("audit emission skipped: dashboard_url not configured")
        return

    funnel_id = report.funnel_id
    if not funnel_id:
        logger.warning("audit emission skipped: report has no funnel_id")
        return

    # Borderline-snapshot intent flag. The actual blob write is deferred (no
    # blob plumbing yet); stamping the flag lets a future blob-snapshotter
    # backfill snapshots without re-reading the report.
    borderline = _is_borderline(report)

    results_summary = {
        "schema_version": report.schema_version,
        "report_id": report.report_id,
        "model_versions": report.model_versions,
        "competence_flag": {
            "in_domain": report.competence_flag.in_domain,
            "therapeutic_area": report.competence_flag.therapeutic_area,
            "reason": report.competence_flag.reason,
        },
        "overall_confidence": report.overall_confidence,
        "overall_confidence_label": report.overall_confidence_label.value,
        "chain": [s.model_dump() for s in report.chain],
        "_borderline_snapshot_pending": borderline,
    }

    # Full report JSON or pointer — for now, embed the full report when small,
    # and stamp a borderline flag when borderline. The blob-snapshot plumbing
    # is a follow-up. Full report is sanitized via Pydantic's mode="json".
    full_report = report.model_dump(mode="json", exclude_none=True)

    sysmeta = {
        "surface": _SURFACE_TAG,
        "schema_version": report.schema_version,
        "model_versions": report.model_versions,
        "request_hash": request_hash,
        "borderline_snapshot_pending": borderline,
        "competence": {
            "in_domain": report.competence_flag.in_domain,
            "therapeutic_area": report.competence_flag.therapeutic_area,
        },
        "full_report": full_report,
    }

    payload = {
        "funnel_id": funnel_id,
        "event_type": "developability_report",
        "tool_name": _SYNTHETIC_TOOL_NAME,
        "stage_name": _SYNTHETIC_TOOL_NAME,
        "stage_label": "Developability Report (REST v1)",
        "tool_arguments": {
            "request_hash": request_hash,
            "screen_format": req.perturbation.screen_format,
            "therapeutic_area": req.therapeutic_area,
            "intended_use": req.intended_use,
            "jurisdiction": req.jurisdiction,
            "include_docking": req.include_docking,
            "target_pdb": req.target_pdb,
            "smiles_prefix": req.smiles[:64],
        },
        "results_summary": results_summary,
        "credits_consumed": 0,  # REST flagship; per-stage credits ride on each sub-call's own audit row
        # Approximate execution time: now − the earliest stage start time. We
        # don't track exact total wall-clock here because the chain stages
        # interleave (parallel gather). The per-stage `latency_ms` on each
        # provenance block is the source of truth for fine-grained timing.
        "execution_time_ms": max(0, _now_ms() - report.generated_at_ms),
        "human_reviewed": False,
        "surface": _SURFACE_TAG,
        "org_id": user.org_id,
        "user_id": user.user_id,
        "system_metadata": sysmeta,
    }

    try:
        async with mcp_tools_module._AUTOLOG_SEMAPHORE:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{dashboard_url}/api/v1/funnel/{funnel_id}/log",
                    json=payload,
                    headers={"X-Admin-Key": admin_key} if admin_key else {},
                )
                if response.status_code >= 400:
                    logger.warning(
                        "audit POST failed for funnel %s: HTTP %s body=%s",
                        funnel_id, response.status_code, response.text[:200],
                    )
    except Exception as e:
        logger.warning("audit emission exception for funnel %s: %s", funnel_id, e)


# ---------------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------------


class _ToolArgumentsEnvelope(BaseModel):
    """Catalog-shape request body: `{"arguments": {<flat request>}}`.

    Mirrors `mcp/router.py:execute_tool` so the canonical route looks identical
    to every other `POST /v1/tools/{name}` call. The `arguments` payload is
    the existing DevelopabilityReportRequest body verbatim — no field renames.
    """
    arguments: DevelopabilityReportRequest


async def _run_developability_chain(
    request: DevelopabilityReportRequest,
    user: MCPUser,
) -> Dict[str, Any]:
    """Shared chain runner used by both the canonical and legacy routes.

    Returns the sanitized report dict (NaN/Inf-stripped, exclude_none-ed) —
    the canonical route wraps this in `{"result": ..., "usage": ...}`; the
    legacy alias returns it flat.
    """
    request_hash = _hash_request(request)

    # Resolve / mint funnel_id. Per the brief, REST-surface audits go through
    # a parallel logger, so we do NOT call executor._resolve_funnel_id (which
    # writes into the MCP per-conversation slot). Use the request-supplied id
    # if present; otherwise mint a synthetic devrep_* id scoped to the user.
    funnel_id = request.funnel_id or _mint_funnel_id(user.user_id)

    normalized = _normalize_perturbation_input(request)
    competence = _classify_competence(request.therapeutic_area)

    admet, faves, clinical, commodity, chain_trail = await _run_chain(
        req=request,
        normalized=normalized,
        competence=competence,
    )

    docking = await _run_docking(req=request, chain_trail=chain_trail)

    report = _assemble_report(
        req=request,
        normalized=normalized,
        competence=competence,
        admet=admet,
        faves=faves,
        clinical=clinical,
        commodity=commodity,
        docking=docking,
        chain_trail=chain_trail,
        funnel_id=funnel_id,
    )

    # Audit emission runs in the background — never block the response on
    # dashboard-aggregator availability. Same pattern as MCP autolog.
    asyncio.create_task(_emit_audit(
        user=user,
        req=request,
        report=report,
        request_hash=request_hash,
    ))

    # NaN/Inf sanitization before serialization. NovoExpert-3 SHAP arrays in
    # particular can produce NaN for compounds whose feature vector has missing
    # values; standard json.dumps would 500 otherwise. _sanitize_for_json
    # recursively replaces NaN/+Inf/-Inf with None, preserving the rest of the
    # payload. exclude_none=True drops the resulting None fields so the audit
    # contract ("clinical_clearance_signal key absent when null") stays intact.
    return _sanitize_for_json(report.model_dump(exclude_none=True))


def _usage_envelope(user: MCPUser) -> Dict[str, Any]:
    """Build the `usage` block matching the catalog-tool response shape from
    `mcp/router.py:execute_tool` (`{"result": ..., "usage": ...}`).

    `credits` is 0 at the orchestration level because per-stage charges ride
    on each sub-call's audit row (addie-models, faves-compliance, novoexpert);
    this matches the existing `credits_consumed: 0` audit comment at the top
    of the chain. `credits_remaining` reads the user's wallet balance.
    """
    remaining = getattr(user, "credits_available", None)
    if remaining is None:
        status = "unknown"
    elif remaining <= 0:
        status = "exhausted"
    elif remaining < 50:
        status = "low"
    else:
        status = "ok"
    return {
        "tool": "developability_report",
        "credits": 0,
        "credits_remaining": remaining,
        "credit_status": status,
    }


_COMMON_DESCRIPTION = (
    "Mode A v1: Direct chemical perturbagen → chemistry signals.\n\n"
    "Returns a structured report assembled from the existing NovoMCP chemistry "
    "chain (NovoExpert-2 ADMET, FAVES V4 compliance, NovoExpert-3 clinical "
    "clearance, optional docking). The report is **informational, not adjudicative** "
    "— no decision, verdict, or recommendation field is returned. The caller's "
    "pipeline owns the advance/flag/deprioritize boundary.\n\n"
    "Bearer auth via `nmcp_` or `ncmcp_` API key. Audit row written to "
    "funnel_audit_log with surface=`rest-v1-developability-report`."
)

_COMMON_RESPONSES = {
    401: {"description": "Missing or invalid API key."},
    422: {"description": "Unsupported screen_format or invalid payload."},
    503: {"description": "Chain executor not initialized."},
}


@router.post(
    "/v1/tools/developability_report",
    summary="Assemble a structured developability-signal report (canonical catalog-shape route).",
    description=(
        _COMMON_DESCRIPTION
        + "\n\n**Canonical catalog-shape route.** Body wraps the request in "
        "`{\"arguments\": {...}}`; response wraps in `{\"result\": {...}, "
        "\"usage\": {tool, credits, credits_remaining, credit_status}}` — "
        "matches every other `/v1/tools/{name}` call shape, so existing "
        "Postman/Insomnia collections and AI-agent loops can call it without "
        "special-case handling. This endpoint is **API-only** (`x-mcp-exposed: "
        "false`) — it's not registered with the MCP server because the chain "
        "orchestration belongs in external pipelines, not LLM-client invocation."
    ),
    response_model_exclude_none=True,
    responses=_COMMON_RESPONSES,
    openapi_extra={
        "x-mcp-exposed": False,
        "x-rest-flagship": True,
        "x-compute": False,
    },
)
async def post_developability_report_canonical(
    envelope: _ToolArgumentsEnvelope,
    raw_request: Request,
    user: MCPUser = Depends(get_mcp_user),
) -> JSONResponse:
    """Canonical catalog-shape route — see module docstring for posture."""
    sanitized_report = await _run_developability_chain(envelope.arguments, user)
    return JSONResponse(content={
        "result": sanitized_report,
        "usage": _usage_envelope(user),
    })


@router.post(
    "/v1/developability-report",
    summary="DEPRECATED — use POST /v1/tools/developability_report.",
    description=(
        _COMMON_DESCRIPTION
        + "\n\n**Deprecated.** This route returns the legacy flat shape (no "
        "`arguments` envelope on input, no `result`/`usage` wrapper on "
        "output). It is preserved verbatim for backwards compatibility with "
        "the T2-D evaluation harness and demo script that shipped against "
        "this URL. New integrations should call `POST /v1/tools/developability"
        "_report` instead."
    ),
    response_model_exclude_none=True,
    responses=_COMMON_RESPONSES,
    deprecated=True,
    openapi_extra={
        "x-deprecation-replacement": "/v1/tools/developability_report",
        "x-rest-flagship": True,
        "x-compute": False,
    },
)
async def post_developability_report_legacy(
    request: DevelopabilityReportRequest,
    raw_request: Request,
    user: MCPUser = Depends(get_mcp_user),
) -> JSONResponse:
    """Legacy flat-shape route — preserved for backwards compat."""
    sanitized = await _run_developability_chain(request, user)
    return JSONResponse(content=sanitized)
