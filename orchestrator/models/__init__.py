"""
Pydantic Models for NovoMCP

Phase 1, Week 1: Core type-safe models replacing Dict[str, Any] patterns.
Provides runtime validation, IDE support, and API documentation.
"""

from .orchestration import (
    OrchestrationRequest,
    OrchestrationResponse,
    WorkflowState,
    PhaseResult,
    PhaseStatus
)

from .service_requests import (
    MolecularIntelligenceRequest,
    ADMETScreeningRequest,
    AutoDockRequest,
    FAVESComplianceRequest,
    GromacsSimulationRequest,
    OptimizationRequest
)

from .quality_gates import (
    QualityGateResult,
    GateFailure,
    ViolationSeverity,
    GateAction
)

from .molecules import (
    Molecule,
    MolecularProperties,
    ADMETProperties,
    DockingResult,
    QuantumResult,
    MoleculeList
)

# Events deliberately NOT eagerly imported at package load.
# events.py uses the Pydantic v1 `Field(..., const=True)` form which raises
# PydanticUserError at module-import time on the production Pydantic 2.x.
# Consumers that need event models can `from models.events import X` directly,
# accepting the v1-vs-v2 risk at that call site. Grep across the codebase
# confirms there are currently no consumers via the `models.X` shortcut path,
# so dropping the eager import is a no-op for existing callers.
#
# This unblocks any new module under `models/` from being importable through
# the package: previously, importing e.g. `models.developability_report`
# would trigger this package __init__, which would crash on `events.py` and
# fail every router that goes through `from models.X import ...`.

__all__ = [
    # Orchestration
    "OrchestrationRequest",
    "OrchestrationResponse",
    "WorkflowState",
    "PhaseResult",
    "PhaseStatus",

    # Service Requests
    "MolecularIntelligenceRequest",
    "ADMETScreeningRequest",
    "AutoDockRequest",
    "FAVESComplianceRequest",
    "GromacsSimulationRequest",
    "OptimizationRequest",

    # Quality Gates
    "QualityGateResult",
    "GateFailure",
    "ViolationSeverity",
    "GateAction",

    # Molecules
    "Molecule",
    "MolecularProperties",
    "ADMETProperties",
    "DockingResult",
    "QuantumResult",
    "MoleculeList",

    # Events (Phase 2) — eager import removed; use `from models.events import X`
]
