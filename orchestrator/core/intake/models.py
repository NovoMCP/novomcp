"""Pydantic models for the intake classifier.

These are the public data shapes the classifier emits and that the rest
of the pipeline (and eventually the /audit endpoint) consumes. Keeping
them in their own module avoids circular imports between classifier,
parser, and metalpdb.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


FunctionalRole = Literal[
    "structural",
    "catalytic",
    "electron",
    "transport",
    "unknown",
]

Route = Literal[
    "run_soluble",       # happy path — no metals, no membrane, no exotic cofactors
    "run_membrane",      # membrane protein — CHARMM36m + packmol-memgen bilayer branch
    "refused",           # classifier declined the system; see reasons
]

ClassificationSource = Literal[
    "metalpdb_pfam",     # Pfam family lookup on MetalPDB response
    "metalpdb_ec",       # EC number fallback when Pfam unknown
    "metalpdb_pattern",  # coordination motif pattern match
    "heuristic",         # donor-atom heuristic (doc 07 Option B)
    "none",              # no metal, or no classification applicable
]

SuggestedBranch = Literal[
    "charmm36m_membrane",   # future: membrane branch (doc 02)
    "mcpb_distal",          # future: MCPB workflow for structural/distal metals
    "qmmm_active_site",     # future: QM/MM for catalytic metals
]


class MetalFinding(BaseModel):
    """A single metal site as seen by the classifier.

    Carries both the locally-computed coordination sphere (from
    MDAnalysis) and any annotation we got back from MetalPDB. Fields
    that come from external sources are optional so the model remains
    valid when MetalPDB has no hit.
    """
    # Local detection
    element: str                              # "Zn", "Mg", "Cu", ...
    chain: Optional[str] = None
    residue_number: int
    coordination_number: int                  # local count
    ligating_residues: List[str] = Field(default_factory=list)  # ["HIS:94:NE2", ...]
    fingerprint: str                          # "Zn: 3xNE2(His) + 1xO(Hoh)"

    # MetalPDB annotation (None when API miss or no hit for this specific metal)
    metalpdb_site_id: Optional[str] = None
    metalpdb_pfam: Optional[str] = None
    metalpdb_ec: Optional[str] = None
    metalpdb_nuclearity: Optional[str] = None      # "Mononuclear" / "Dinuclear" / ...
    metalpdb_geometry: Optional[str] = None        # "tetrahedron (regular)" / ...
    metalpdb_pattern: Optional[str] = None         # "HX(1)HX(22)H" / ...
    metalpdb_coord_number: Optional[int] = None    # API-side coordination count
    metalpdb_molecule: Optional[str] = None        # "Carbonic anhydrase 2"

    # Classification
    functional_role: FunctionalRole = "unknown"
    classification_source: ClassificationSource = "none"
    classification_reason: str = ""

    # CheckMyMetal-style sanity check: does local cn agree with MetalPDB cn?
    geometry_sanity_ok: Optional[bool] = None


class SystemProfile(BaseModel):
    """Everything the classifier learned about the input structure.

    This is the audit-ready view that also feeds downstream routing.
    Fields are populated best-effort — partial profiles are still
    valid when external APIs fail or the parser struggles.
    """
    pdb_id: Optional[str] = None

    # Membrane detection
    is_membrane: bool = False
    opm_source: Optional[str] = None          # "opm_api" | "cache" | None

    # Metal sites
    metal_sites: List[MetalFinding] = Field(default_factory=list)

    # HETATM audit
    heme_residues: List[str] = Field(default_factory=list)    # ["HEM"] etc.
    fes_clusters: Dict[str, int] = Field(default_factory=dict)  # {"SF4": 2}
    hetatm_inventory: List[str] = Field(default_factory=list)   # other non-standard residues

    # PTMs (doc 04 scope — placeholder for v0, populated in later versions)
    ptm_residues: List[str] = Field(default_factory=list)

    # Parser + warnings
    parser_used: Literal["mdanalysis", "biopython", "failed"] = "mdanalysis"
    warnings: List[str] = Field(default_factory=list)


class RoutingDecision(BaseModel):
    """Classifier's verdict. Never an exception — always a structured result.

    `route` is the machine-readable decision; `reasons` is the ordered
    list of human-readable explanations the user sees; `profile` carries
    the full audit view; `suggested_branch` is a forward-looking hint
    pointing at future branches the system would route to once they
    exist.
    """
    route: Route
    reasons: List[str] = Field(default_factory=list)
    profile: SystemProfile
    suggested_branch: Optional[SuggestedBranch] = None
