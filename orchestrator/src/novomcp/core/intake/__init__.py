"""Intake classifier — production gatekeeper for the gromacs-md pipeline.

Public API:
    classify_structure(pdb_content, pdb_id=None, redis_client=None) -> RoutingDecision
    SystemProfile
    RoutingDecision
    MetalFinding

Replaces the old `clean_pdb_protein_only()` silent-strip behavior with
structured classification. Membrane proteins, metalloproteins, heme
cofactors, and Fe-S clusters now produce specific refusals with
`suggested_branch` hints, rather than being deleted from the structure
and silently running through the soluble pipeline.

See planning/hard-systems/ for the full design history.
"""

from .classifier import classify_structure
from .models import (
    ClassificationSource,
    FunctionalRole,
    MetalFinding,
    Route,
    RoutingDecision,
    SuggestedBranch,
    SystemProfile,
)
from .pfam import table_size as pfam_table_size

__all__ = [
    "classify_structure",
    "ClassificationSource",
    "FunctionalRole",
    "MetalFinding",
    "Route",
    "RoutingDecision",
    "SuggestedBranch",
    "SystemProfile",
    "pfam_table_size",
]
