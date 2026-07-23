"""
Developability Report — Pydantic v2 models for POST /v1/developability-report.

The developability-report endpoint is the REST-only top-level flagship surface
that takes a perturbation result (compound SMILES + observed cellular effect
from a screen — LINCS, Perturb-seq, virtual-cell output, etc.) and assembles a
structured developability-signal report by calling the existing chemistry
stack:

  - NovoExpert-2 ADMET via addie-models       (toxicity + CYP)
  - FAVES V4 compliance via faves-compliance  (regulatory + structural alerts)
  - NovoExpert-3 clinical-outcomes via addie-models / novoexpert
  - Optional docking via autodock-gpu when `include_docking=true` AND
    `target_pdb` supplied in the request body.

Posture (locked — see brief and CLAUDE.md):

  - **FAVES posture: informational, NOT adjudicative.** The report contains
    structured signals; the caller decides what `advance`/`flag`/`deprioritize`
    means in their own pipeline. There is intentionally NO `Decision`,
    `Verdict`, or `Recommendation` field in any model in this module. Anyone
    tempted to add one should re-read the brief — it is the load-bearing
    liability boundary. Caller decides.

  - **REST-only.** This endpoint is NOT exposed as an MCP tool. The MCP tool
    surface stays at /v1/tools/{name}; this report is a flagship top-level
    /v1/developability-report path.

  - **Mode A v1.** Direct chemical perturbagen → chemistry signals. Mode B
    (gene-knockdown surrogate) and Mode C (virtual-cell trajectory) are out of
    scope. See `screen_format` discriminator.

  - **Engine-first language.** This is the engine's report. Never use
    "verdict", "decision", or "substrate" in user-facing prose.

Provenance and the open-source swap-out path:

  - The moat is the chain (integration + audit + signal stitching + calibration),
    NOT exclusive use of proprietary models. Every chain link's `provenance`
    block carries `model_name`, `model_version`, `training_data_snapshot_hash`,
    `model_card_url`, and `open_source_alternative_validated`. The last field
    is nullable per stage to support flipping to an OSS model when the
    swap-out criterion (independent re-evaluation parity) is met.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

# Schema version stamped on every report row + audit row. Bump this whenever
# the response shape changes in a way callers can observe (added/removed
# fields, renamed fields, semantics shift). Additive changes to provenance
# blocks alone do not bump.
DEVELOPABILITY_REPORT_SCHEMA_VERSION = "1.1.0"
# 1.1.0 (2026-06-24): added `cardiotoxicity_dict_probability` (validated DICTrank
# head). `cardiotoxicity_max_probability` is DEPRECATED — the legacy base-ADDIE
# head was retired (a prior release); it is now populated from cardiotoxicity_dict
# for back-compat. Additive + back-compat, so no caller breaks.


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------


class ConfidenceLabel(str, Enum):
    """Categorical confidence label paired with a numeric [0.0, 1.0] score.

    The numeric is the source of truth; the label is a presentation aid for
    callers that do not want to thresh­old themselves. Bucket boundaries are
    intentionally fixed and not configurable on the request path — variability
    in the label across calls undermines auditability.

    Bucket boundaries:
      very_low  : [0.00, 0.20)
      low       : [0.20, 0.40)
      moderate  : [0.40, 0.60)
      high      : [0.60, 0.80)
      very_high : [0.80, 1.00]
    """

    VERY_LOW = "very_low"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


def confidence_label_for(score: Optional[float]) -> Optional[ConfidenceLabel]:
    """Map a numeric confidence in [0.0, 1.0] to its categorical label.

    Returns None when the score is None (degraded / not run). Out-of-range
    scores are clamped to the nearest bucket — the bucket boundaries are the
    contract, not the input value, so silent clamping is safer than raising
    inside the assembly path. Validation of the upstream score happens at
    ingestion (Pydantic Field constraints).
    """
    if score is None:
        return None
    if score < 0.20:
        return ConfidenceLabel.VERY_LOW
    if score < 0.40:
        return ConfidenceLabel.LOW
    if score < 0.60:
        return ConfidenceLabel.MODERATE
    if score < 0.80:
        return ConfidenceLabel.HIGH
    return ConfidenceLabel.VERY_HIGH


class FavesOverallStatus(str, Enum):
    """FAVES informational status. NOT a recommendation.

    Mirrors the upstream FAVES V4 vocabulary as normalized in
    `_execute_check_compliance`. Caller decides what action — if any — to
    take based on the status, alerts, and regulatory_pathway. The engine does
    not roll this up.
    """

    PROCEED = "PROCEED"
    CAUTION = "CAUTION"
    STOP = "STOP"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Provenance — attached to every chain link
# ---------------------------------------------------------------------------


class Provenance(BaseModel):
    """Per-stage provenance block.

    Every chain link records its own `Provenance` block. The auditor's
    contract is: given the full report, you can identify exactly which model
    version produced each number, and you can fetch its model card and the
    training-data manifest hash. The `open_source_alternative_validated`
    field is nullable so the engine can flip individual stages to OSS-backed
    inference when a stage's swap-out criterion is met without bumping the
    whole report schema.
    """

    model_config = ConfigDict(extra="allow")

    model_name: str = Field(..., description="Logical model name, e.g. 'NovoExpert-2-ADMET'.")
    model_version: str = Field(..., description="Semver string from the downstream service.")
    source_service: str = Field(..., description="Downstream service that produced the signal.")
    called_at_ms: int = Field(..., description="UTC epoch milliseconds when this stage was dispatched.")
    latency_ms: int = Field(..., ge=0, description="Wall-clock latency of the stage call.")
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Stage-level confidence in [0.0, 1.0]. None when the stage did not return one.",
    )
    training_data_snapshot_hash: Optional[str] = Field(
        default=None,
        description="SHA256 hex of the training-data manifest. Nullable for rules-based stages (FAVES).",
    )
    model_card_url: Optional[str] = Field(
        default=None,
        description="Public URL of the model card. Nullable when none is yet published.",
    )
    open_source_alternative_validated: Optional[bool] = Field(
        default=None,
        description=(
            "Flag for the moat-plan swap-out path: True iff this stage has been "
            "validated against a published OSS baseline at parity, False iff "
            "validated and the proprietary model wins, None iff not yet evaluated."
        ),
    )
    degraded: bool = Field(
        default=False,
        description=(
            "True iff the stage failed and the report falls back to a degraded "
            "default for this block. Callers should treat degraded stages as "
            "missing signal, not as low-confidence signal."
        ),
    )
    degraded_reason: Optional[str] = Field(
        default=None,
        description="Brief machine-readable reason when degraded=True.",
    )


# ---------------------------------------------------------------------------
# ADMET / liability signal block
# ---------------------------------------------------------------------------


class AdmetLiability(BaseModel):
    """ADMET liability block stitched from NovoExpert-2 + addie-models output.

    Numbers are signals, not decisions. The caller's pipeline applies its own
    threshold logic for filter / advance / deprioritize.
    """

    model_config = ConfigDict(extra="allow")

    cyp_inhibition_risk_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Aggregate CYP inhibition risk [0.0, 1.0]. NovoExpert-2 SOTA on CYP3A4/2D6.",
    )
    cyp_substrate_max_probability: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Max P(substrate) across CYP3A4/2D6/2C9.",
    )
    hepatotoxicity_probability: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    cardiotoxicity_dict_probability: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Cardiotoxicity probability from the validated DICTrank head "
            "(FDA Liu-2023, calibrated). Canonical cardiotox signal as of schema 1.1.0."
        ),
    )
    cardiotoxicity_max_probability: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "DEPRECATED (schema 1.1.0): the legacy base-ADDIE cardiotoxicity_max head "
            "was retired (a prior release). Now mirrors cardiotoxicity_dict_probability "
            "for back-compat; prefer cardiotoxicity_dict_probability."
        ),
    )
    herg_blocker_probability: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    ames_mutagenicity_probability: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    overall_toxicity_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Aggregate toxicity score across the 31 addie-models endpoints.",
    )
    raw_categories: Optional[Dict[str, Dict[str, Optional[float]]]] = Field(
        default=None,
        description=(
            "Optional pass-through of the full categorical breakdown "
            "(absorption/distribution/metabolism/excretion/toxicity/etc.) "
            "from _execute_predict_admet. Populated when "
            "request.include_raw_categories=true."
        ),
    )
    provenance: Provenance


# ---------------------------------------------------------------------------
# FAVES compliance signal block (informational, NOT adjudicative)
# ---------------------------------------------------------------------------


class StructuralAlert(BaseModel):
    """A single structural alert as surfaced by FAVES V4.

    `severity` is the FAVES-assigned tag; downstream consumers decide whether
    to treat severity=high as a filter. The engine surfaces it verbatim.
    """

    model_config = ConfigDict(extra="allow")

    alert_id: str
    alert_name: str
    severity: Optional[str] = None
    matched_smarts: Optional[str] = None
    notes: Optional[str] = None


class FavesCompliance(BaseModel):
    """FAVES V4 compliance signal.

    Informational. The `overall_status` is FAVES's own normalized label and is
    not a recommendation from the engine. The caller's pipeline owns the
    decision boundary.
    """

    model_config = ConfigDict(extra="allow")

    overall_status: FavesOverallStatus = Field(
        ...,
        description="FAVES V4 normalized status: PROCEED, CAUTION, STOP, unknown. INFORMATIONAL.",
    )
    raw_overall_status: Optional[str] = Field(
        default=None,
        description="Original FAVES V4 status before normalization, for audit forensics.",
    )
    structural_alerts: List[StructuralAlert] = Field(default_factory=list)
    has_pains: Optional[bool] = None
    is_aggregator_risk: Optional[bool] = None
    boiled_egg_class: Optional[str] = Field(
        default=None,
        description="BOILED-Egg class: white/yolk/grey/etc. for HIA/BBB at-a-glance.",
    )
    regulatory_pathway: Optional[Union[str, Dict[str, Any]]] = Field(
        default=None,
        description=(
            "FAVES-assessed regulatory pathway. Informational. "
            "Production FAVES returns a structured dict (`{pathway: str, steps: [str, ...]}`); "
            "older / leaner deployments may return a bare string. Accepts either shape and "
            "preserves the payload as-is for the caller to interpret. Matches the FAVES posture: "
            "surface signals, don't normalize away upstream structure."
        ),
    )
    jurisdiction: Optional[str] = Field(
        default=None,
        description="Jurisdiction the FAVES check ran against (echoed from request).",
    )
    intended_use: Optional[str] = Field(
        default=None,
        description="Intended-use string echoed from request.",
    )
    provenance: Provenance


# ---------------------------------------------------------------------------
# Clinical clearance signal block
# ---------------------------------------------------------------------------


class ShapFeature(BaseModel):
    """Single top-k SHAP feature attribution from NovoExpert-3."""

    model_config = ConfigDict(extra="allow")

    feature_name: str
    contribution: float
    rank: int


class ClinicalClearanceSignal(BaseModel):
    """NovoExpert-3 Phase I clinical clearance signal.

    Only populated when `competence_flag.in_domain == True`. When the request
    falls outside NovoExpert-3's validated domain (oncology / CNS / infectious
    / unknown therapeutic_area), this block is `None` on the response and the
    `competence_flag` at the top level explains why.
    """

    model_config = ConfigDict(extra="allow")

    phase1_clearance_probability: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    phase1_clearance_probability_raw: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Pre-calibration raw probability for forensic comparison.",
    )
    calibration: Optional[str] = Field(default=None, description="Calibration method, e.g. 'isotonic'.")
    feature_count: Optional[int] = Field(default=None, ge=0)
    missing_features: List[str] = Field(default_factory=list)
    top_shap_features: List[ShapFeature] = Field(default_factory=list)
    feature_sources: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Per-service success/failure breakdown ({succeeded:[], failed:[]}).",
    )
    provenance: Provenance


# ---------------------------------------------------------------------------
# Clinical competence flag — top-level, NOT nested
# ---------------------------------------------------------------------------


class CompetenceFlag(BaseModel):
    """Clinical-domain competence gate.

    Top-level field on the report, NOT nested in the clinical block. Per
    NovoExpert-3's documented bimodal performance:

      Competent (in-domain):
        - cardiovascular (CV)   AUROC 0.76
        - gastrointestinal (GI) AUROC 0.80
        - mainstream            AUROC 0.72

      Not validated (out-of-domain):
        - oncology              AUROC 0.47
        - CNS                   AUROC 0.48
        - infectious            AUROC 0.36
        - unknown               (not eligible)

    When `in_domain=False`, the report MUST set `clinical_clearance_signal=None`
    so the engine never surfaces a low-confidence clinical number for compounds
    outside the validated domain.
    """

    model_config = ConfigDict(extra="allow")

    in_domain: bool = Field(
        ...,
        description="True iff the request's therapeutic_area is in the validated allow-list.",
    )
    therapeutic_area: str = Field(..., description="Therapeutic area from the request (echoed).")
    reason: str = Field(
        ...,
        description="Human-readable explanation. Always populated, even when in_domain=True.",
    )
    allow_list: List[str] = Field(
        default_factory=lambda: ["cardiovascular", "gastrointestinal", "mainstream"],
        description="The in-domain allow-list this report was evaluated against.",
    )


# ---------------------------------------------------------------------------
# Commodity context — reflective only, NOT a decision
# ---------------------------------------------------------------------------


class CommodityContext(BaseModel):
    """Commodity / known-actives context for the compound.

    Reflective only. Indicates whether the compound is a known drug, in the
    FDA whitelist, has a high ChEMBL match count, etc. Caller decides whether
    to deprioritize commodity hits in their own pipeline.
    """

    model_config = ConfigDict(extra="allow")

    is_known_drug: Optional[bool] = None
    is_fda_whitelisted: Optional[bool] = None
    chembl_match_count: Optional[int] = Field(default=None, ge=0)
    pubchem_cid: Optional[int] = Field(default=None, ge=0)
    commodity_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Aggregate commodity score [0.0, 1.0]. Informational only.",
    )
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Docking block (optional, on-request only)
# ---------------------------------------------------------------------------


class DockingPose(BaseModel):
    """A single docking pose returned by autodock-gpu."""

    model_config = ConfigDict(extra="allow")

    rank: int
    binding_affinity_kcal_mol: float
    rmsd_lb: Optional[float] = None
    rmsd_ub: Optional[float] = None


class DockingBlock(BaseModel):
    """Docking signal block. Present iff `include_docking=true` AND `target_pdb` supplied.

    Per Harrison's locked default (see brief): docking is on-request only. When
    not requested, the entire `docking` field on the report is **absent**
    (not present-with-null). This is enforced in the assembly path via
    `model_dump(exclude_none=True)` plus explicit drop of the key.
    """

    model_config = ConfigDict(extra="allow")

    target_pdb: str
    poses: List[DockingPose] = Field(default_factory=list)
    best_binding_affinity_kcal_mol: Optional[float] = None
    provenance: Provenance


# ---------------------------------------------------------------------------
# Request models — discriminated union over screen_format
# ---------------------------------------------------------------------------


class GenericPerturbation(BaseModel):
    """Generic perturbation payload — the canonical Mode A v1 shape.

    Use this when the caller has SMILES + a phenotype string + (optionally) a
    cell-context label, regardless of which platform produced the screen.
    """

    model_config = ConfigDict(extra="allow")

    screen_format: Literal["generic"] = "generic"
    smiles: str = Field(..., min_length=1, description="Compound SMILES.")
    phenotype: str = Field(
        ...,
        description="Observed cellular phenotype string (free text from the screen).",
    )
    cell_context: Optional[str] = Field(
        default=None,
        description="Cell line / tissue context (e.g., 'K562', 'A549', 'primary hepatocytes').",
    )
    desired_effect: Optional[str] = Field(
        default=None,
        description=(
            "Caller-supplied criterion for what 'good' means in their pipeline. "
            "Recorded in the audit chain alongside the report. The engine does "
            "NOT evaluate against it (no decision logic) — but downstream "
            "auditors can replay the report knowing what the caller was after."
        ),
    )


class LincsPerturbation(BaseModel):
    """LINCS L1000-style perturbation payload.

    Maps the LINCS triple (perturbagen / signature / cell_id) into the chain
    inputs. The signature gene-list is captured verbatim and surfaced as
    `top_signature_genes` in the audit row; we do NOT use it as a chain input
    in Mode A v1 (that's Mode B/C territory).
    """

    model_config = ConfigDict(extra="allow")

    screen_format: Literal["lincs"] = "lincs"
    smiles: str = Field(..., min_length=1, description="Perturbagen compound SMILES.")
    pert_id: Optional[str] = Field(default=None, description="LINCS pert_id, e.g. 'BRD-A12345'.")
    cell_id: Optional[str] = Field(default=None, description="LINCS cell_id, e.g. 'A549'.")
    signature_top_up: List[str] = Field(
        default_factory=list,
        description="LINCS L1000 signature: top-N up-regulated gene symbols.",
    )
    signature_top_down: List[str] = Field(
        default_factory=list,
        description="LINCS L1000 signature: top-N down-regulated gene symbols.",
    )
    desired_effect: Optional[str] = None


# Discriminated union — Pydantic v2 picks the model by `screen_format`.
# Unsupported screen_format values surface as 422 with detail.error_code =
# "unsupported_screen_format" via the router's exception handler.
PerturbationPayload = Annotated[
    Union[GenericPerturbation, LincsPerturbation],
    Field(discriminator="screen_format"),
]


class DevelopabilityReportRequest(BaseModel):
    """POST /v1/developability-report request.

    A single perturbation triggers a single report; for batches, callers POST
    one request per perturbation and may use HTTP/2 multiplexing. Per the brief
    section 7.2, when a batch endpoint is added later, malformed entries
    return a structured per-entry error so N-1 reports + 1 error is the shape.
    """

    model_config = ConfigDict(extra="forbid")

    perturbation: PerturbationPayload = Field(
        ...,
        description="The perturbation payload — generic or LINCS-style.",
    )
    therapeutic_area: str = Field(
        ...,
        description=(
            "Therapeutic area driving the clinical competence gate. "
            "Allow-list: cardiovascular, gastrointestinal, mainstream. "
            "Out-of-domain (oncology / CNS / infectious / unknown) surfaces "
            "as a top-level competence_flag and suppresses the clinical block."
        ),
    )
    intended_use: str = Field(
        ...,
        description="Intended-use string passed to FAVES (e.g. 'pharmaceutical').",
    )
    jurisdiction: str = Field(
        default="US",
        description="Regulatory jurisdiction for FAVES context (US/EU/PMDA).",
    )
    target_pdb: Optional[str] = Field(
        default=None,
        description=(
            "PDB ID for optional docking. Required iff `include_docking=true`."
        ),
    )
    include_docking: bool = Field(
        default=False,
        description=(
            "When true (and target_pdb is supplied), the engine also runs "
            "autodock-gpu and includes a `docking` block. Default false — "
            "docking is on-request only per locked posture."
        ),
    )
    include_raw_categories: bool = Field(
        default=False,
        description="Pass through the full ADMET category breakdown in the response.",
    )
    funnel_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional funnel_id to bind this report into an existing audit "
            "thread. When omitted, the engine mints a synthetic "
            "`devrep_<timestamp>_<short_hash>` slot scoped to the caller."
        ),
    )

    @property
    def smiles(self) -> str:
        """Convenience accessor — pulls SMILES out of the discriminated union."""
        return self.perturbation.smiles


# ---------------------------------------------------------------------------
# Response model — DevelopabilityReport
# ---------------------------------------------------------------------------


class ChainStageStatus(BaseModel):
    """Per-stage status in the chain execution trail.

    Lets callers see at a glance which downstream services were called, which
    succeeded, which were skipped (e.g., docking when not requested), and
    which were suppressed (e.g., clinical when out-of-domain).
    """

    model_config = ConfigDict(extra="allow")

    stage: str
    status: Literal["ok", "degraded", "skipped", "suppressed"]
    reason: Optional[str] = None


class DevelopabilityReport(BaseModel):
    """Top-level developability-signal report response.

    Structure (per §7 of the moat plan as locked in the brief):

      - schema_version       always populated
      - report_id            uuid4 hex, generated server-side
      - smiles               compound under report
      - therapeutic_area     echoed from request
      - competence_flag      TOP-LEVEL — not nested in clinical
      - overall_confidence   TOP-LEVEL numeric + label
      - admet_liability      ADMET signal block
      - faves_compliance     FAVES signal block (informational)
      - clinical_clearance_signal  None when out-of-domain
      - commodity_context    reflective
      - docking              absent when include_docking=false
      - chain                per-stage status trail
      - model_versions       flat {stage_name: model_version} for fast audit
      - normalized_input     normalized perturbation payload echo
      - generated_at_ms      UTC epoch ms
    """

    # extra="allow" so downstream services can pass through additional fields
    # without forcing a schema bump (e.g., a new SHAP variant). The brief's
    # liability boundary — no decision field — is enforced at the assembly
    # path, not via extra="forbid" here (which would silently drop them).
    model_config = ConfigDict(extra="allow")

    schema_version: str = Field(
        default=DEVELOPABILITY_REPORT_SCHEMA_VERSION,
        description="Report-schema version. Mandatory on every report row + audit row.",
    )
    report_id: str
    smiles: str
    therapeutic_area: str
    competence_flag: CompetenceFlag = Field(
        ...,
        description=(
            "Clinical competence gate at the top level. When in_domain=False, "
            "clinical_clearance_signal is None and the engine does not surface "
            "a clinical clearance number."
        ),
    )
    overall_confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Aggregate stage-confidence across the chain (mean of available "
            "stage confidences). This is a confidence-in-the-signal score, "
            "NOT a developability score. It does NOT mean 'advance' or "
            "'deprioritize' — the caller decides."
        ),
    )
    overall_confidence_label: ConfidenceLabel
    admet_liability: Optional[AdmetLiability] = None
    faves_compliance: Optional[FavesCompliance] = None
    clinical_clearance_signal: Optional[ClinicalClearanceSignal] = Field(
        default=None,
        description=(
            "NovoExpert-3 clinical clearance signal. None when "
            "competence_flag.in_domain=False or when the stage degraded."
        ),
    )
    commodity_context: Optional[CommodityContext] = None
    docking: Optional[DockingBlock] = Field(
        default=None,
        description=(
            "Docking block. Absent (not present-with-null) when "
            "include_docking=false. Present and populated when the request "
            "asked for docking AND it succeeded."
        ),
    )
    chain: List[ChainStageStatus] = Field(
        default_factory=list,
        description="Per-stage execution trail for at-a-glance debugging.",
    )
    model_versions: Dict[str, str] = Field(
        default_factory=dict,
        description="Flat {stage_name: model_version} for fast audit-row indexing.",
    )
    normalized_input: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Echo of the perturbation payload after normalization. Mode A v1 "
            "always reduces to {smiles, phenotype, cell_context?, desired_effect?}; "
            "the original screen_format is preserved on a separate key for "
            "auditability."
        ),
    )
    generated_at_ms: int = Field(..., description="UTC epoch ms when the report was assembled.")
    funnel_id: Optional[str] = Field(
        default=None,
        description="Funnel slot this report was audited under.",
    )

    # NOTE: Intentionally NO `decision`, `verdict`, `recommendation`, `advance`,
    # `flag`, or `deprioritize` field. The brief is explicit. If you find
    # yourself wanting one, the caller's pipeline is the right place — not this
    # response.
