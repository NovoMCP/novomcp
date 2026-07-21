"""
Service Request Models

Pydantic models for requests to microservices.
Replaces Dict[str, Any] in service_proxy.py calls.
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, validator


class MolecularIntelligenceRequest(BaseModel):
    """
    Request model for molecular-intelligence service

    Replaces: Dict passed to call_molecular_intelligence() in service_proxy.py
    """
    # Core parameters
    batch_size: int = Field(default=200, ge=1, le=5000, description="Number of molecules to generate")
    campaign_id: str = Field(..., description="Campaign identifier for context")

    # Molecular constraints
    constraints: Optional[Dict[str, Any]] = Field(default=None, description="Molecular property constraints (MW, LogP, etc.)")

    # Data source
    dataset: str = Field(default="drug-like", description="Dataset to query: 'drug-like', 'lead-like', 'fragment'")

    # Filters
    similarity_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Tanimoto similarity threshold")
    seed_molecules: Optional[List[str]] = Field(default=None, description="SMILES strings for similarity search")

    # Literature enrichment
    enrich_with_literature: bool = Field(default=True, description="Include literature context")
    therapeutic_area: Optional[str] = Field(default=None, description="Target therapeutic area")

    class Config:
        json_schema_extra = {
            "example": {
                "batch_size": 200,
                "campaign_id": "campaign-abc-123",
                "constraints": {
                    "mw": {"min": 200, "max": 500},
                    "logp": {"min": -0.5, "max": 5.0}
                },
                "dataset": "drug-like"
            }
        }


class ADMETScreeningRequest(BaseModel):
    """
    Request model for ADMET screening

    Note: ADMET is currently in workflow_engine.py, not a separate service.
    This model prepares for Phase 2 event-driven architecture.
    """
    molecules: List[Dict[str, Any]] = Field(..., description="Molecules to screen")
    campaign_id: str = Field(..., description="Campaign identifier")

    # Thresholds
    thresholds: Optional[Dict[str, Any]] = Field(default=None, description="ADMET threshold overrides")

    # Filters to apply
    filters: List[str] = Field(
        default_factory=lambda: ["toxicity", "hepatotoxicity", "cardiotoxicity", "clearance"],
        description="ADMET filters to apply"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "molecules": [{"smiles": "CCO", "id": "mol-1"}],
                "campaign_id": "campaign-abc-123",
                "thresholds": {
                    "toxicity": 0.5,
                    "hepatotoxicity": 0.3
                }
            }
        }


class AutoDockRequest(BaseModel):
    """
    Request model for autodock-gpu service

    Replaces: Dict passed to call_autodock_gpu() in service_proxy.py
    """
    molecules: List[Dict[str, Any]] = Field(..., description="Molecules to dock")
    campaign_id: str = Field(..., description="Campaign identifier")

    # Docking configuration
    target_protein: str = Field(..., description="Target protein PDB/PDBQT file or identifier")
    grid_center: Optional[List[float]] = Field(default=None, description="Grid center [x, y, z]")
    grid_size: Optional[List[float]] = Field(default=None, description="Grid size [x, y, z]")

    # Docking parameters
    exhaustiveness: int = Field(default=8, ge=1, le=32, description="Search exhaustiveness")
    num_modes: int = Field(default=9, ge=1, le=20, description="Number of binding modes")

    # GPU configuration
    max_concurrent: int = Field(default=5, ge=1, le=10, description="Max concurrent docking operations")

    class Config:
        json_schema_extra = {
            "example": {
                "molecules": [{"smiles": "CCO", "id": "mol-1"}],
                "campaign_id": "campaign-abc-123",
                "target_protein": "6LU7",
                "exhaustiveness": 8
            }
        }


class FAVESComplianceRequest(BaseModel):
    """
    Request model for faves-compliance service

    Replaces: Dict passed to call_faves_compliance() in service_proxy.py
    """
    molecules: List[Dict[str, Any]] = Field(..., description="Molecules to validate")
    campaign_id: str = Field(..., description="Campaign identifier")

    # Compliance checks
    checks: List[str] = Field(
        default_factory=lambda: ["ethics", "safety", "regulatory"],
        description="Compliance checks to perform"
    )

    # Regulatory jurisdiction
    jurisdiction: str = Field(default="FDA", description="Regulatory jurisdiction: 'FDA', 'EMA', 'PMDA'")

    # Thresholds
    thresholds: Optional[Dict[str, Any]] = Field(default=None, description="Compliance threshold overrides")

    class Config:
        json_schema_extra = {
            "example": {
                "molecules": [{"smiles": "CCO", "id": "mol-1"}],
                "campaign_id": "campaign-abc-123",
                "checks": ["ethics", "safety", "regulatory"],
                "jurisdiction": "FDA"
            }
        }


class GromacsSimulationRequest(BaseModel):
    """
    Request model for gromacs-md service

    Replaces: Dict passed to call_gromacs_md() in service_proxy.py
    """
    molecules: List[Dict[str, Any]] = Field(..., description="Molecules for MD simulation")
    campaign_id: str = Field(..., description="Campaign identifier")

    # Simulation parameters
    simulation_time_ns: float = Field(default=10.0, ge=1.0, le=100.0, description="Simulation time in nanoseconds")
    temperature_k: float = Field(default=310.0, ge=273.0, le=400.0, description="Temperature in Kelvin (310K = 37°C)")
    pressure_bar: float = Field(default=1.0, ge=0.1, le=10.0, description="Pressure in bar")

    # Force field
    force_field: str = Field(default="OPLS-AA", description="Force field: 'OPLS-AA', 'AMBER', 'CHARMM'")

    # Analysis
    calculate_rmsd: bool = Field(default=True, description="Calculate RMSD over trajectory")
    calculate_rmsf: bool = Field(default=True, description="Calculate RMSF (flexibility)")

    # Async notification (Phase 2)
    callback_queue: Optional[str] = Field(default=None, description="SQS queue for completion notification")

    class Config:
        json_schema_extra = {
            "example": {
                "molecules": [{"smiles": "CCO", "id": "mol-1"}],
                "campaign_id": "campaign-abc-123",
                "simulation_time_ns": 10.0,
                "temperature_k": 310.0
            }
        }


class OptimizationRequest(BaseModel):
    """
    Request model for molmim-optimizer and lead-optimization services

    Replaces: Dict passed to optimization service calls in service_proxy.py
    """
    molecules: List[Dict[str, Any]] = Field(..., description="Molecules to optimize")
    campaign_id: str = Field(..., description="Campaign identifier")

    # Optimization targets
    objectives: List[str] = Field(
        default_factory=lambda: ["binding_affinity", "admet", "synthesizability"],
        description="Optimization objectives"
    )

    # Constraints
    maintain_scaffold: bool = Field(default=True, description="Maintain core scaffold during optimization")
    max_modifications: int = Field(default=3, ge=1, le=10, description="Maximum number of modifications per molecule")

    # Search parameters
    num_iterations: int = Field(default=10, ge=1, le=100, description="Optimization iterations")
    population_size: int = Field(default=50, ge=10, le=500, description="Population size for genetic algorithm")

    class Config:
        json_schema_extra = {
            "example": {
                "molecules": [{"smiles": "CCO", "id": "mol-1"}],
                "campaign_id": "campaign-abc-123",
                "objectives": ["binding_affinity", "admet"],
                "max_modifications": 3
            }
        }
