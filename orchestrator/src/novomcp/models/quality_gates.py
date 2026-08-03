"""
Quality Gate Models

Pydantic models for quality gate evaluation results.
Replaces Dict[str, Any] quality gate results in workflow_engine.py
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from pydantic import BaseModel, Field


class ViolationSeverity(str, Enum):
    """Severity of a quality gate violation"""
    CRITICAL = "critical"  # Must fix
    HIGH = "high"  # Should fix
    MEDIUM = "medium"  # Nice to fix
    LOW = "low"  # Informational


class GateAction(str, Enum):
    """Recommended action for quality gate failure"""
    ADJUST_PARAMETERS = "adjust_parameters"  # Auto-adjust via Strategy pattern
    REGENERATE = "regenerate"  # Regenerate molecules
    ESCALATE_TO_AI = "escalate_to_ai"  # Use LLM for strategy (Phase 3)
    HUMAN_INTERVENTION = "human_intervention"  # Require manual review
    ABORT = "abort"  # Critical failure, abort iteration


class GateFailure(BaseModel):
    """
    Details of a single quality gate failure

    Replaces: Dict in gate failure lists
    """
    molecule_id: str = Field(..., description="Molecule identifier")
    smiles: Optional[str] = Field(default=None, description="SMILES string")

    # Violation details
    violations: List[str] = Field(..., description="List of violated criteria")
    severity: ViolationSeverity = Field(..., description="Failure severity")

    # Values
    actual_values: Dict[str, float] = Field(default_factory=dict, description="Actual property values")
    threshold_values: Dict[str, float] = Field(default_factory=dict, description="Threshold values")

    # Metadata
    reason: Optional[str] = Field(default=None, description="Human-readable failure reason")
    can_adjust: bool = Field(default=True, description="Whether auto-adjustment is possible")

    class Config:
        json_schema_extra = {
            "example": {
                "molecule_id": "mol-123",
                "smiles": "CCO",
                "violations": ["toxicity", "hepatotoxicity"],
                "severity": "high",
                "actual_values": {"toxicity": 0.85, "hepatotoxicity": 0.72},
                "threshold_values": {"toxicity": 0.5, "hepatotoxicity": 0.3},
                "reason": "Toxicity score 0.85 exceeds threshold 0.5"
            }
        }


class QualityGateResult(BaseModel):
    """
    Result from evaluating a quality gate

    Replaces: Dict[str, Any] gate results in workflow_engine.py
    """
    gate_id: str = Field(..., description="Quality gate identifier")
    gate_name: str = Field(..., description="Human-readable gate name")
    passed: bool = Field(..., description="Whether gate passed")

    # Metrics
    molecules_evaluated: int = Field(..., ge=0, description="Number of molecules evaluated")
    molecules_passed: int = Field(..., ge=0, description="Number of molecules that passed")
    molecules_failed: int = Field(..., ge=0, description="Number of molecules that failed")
    pass_rate: float = Field(..., ge=0.0, le=1.0, description="Pass rate (0.0-1.0)")

    # Failure details
    failures: List[GateFailure] = Field(default_factory=list, description="Detailed failure information")
    primary_failure_type: Optional[str] = Field(default=None, description="Most common failure type")
    failure_breakdown: Dict[str, int] = Field(default_factory=dict, description="Count of failures by type")

    # Recommendations
    action: GateAction = Field(..., description="Recommended action")
    can_auto_adjust: bool = Field(default=False, description="Whether auto-adjustment is available")
    adjustment_suggestions: List[str] = Field(default_factory=list, description="Suggested parameter adjustments")

    # Metadata
    execution_time_seconds: float = Field(default=0.0, ge=0.0, description="Gate evaluation time")
    threshold_used: Optional[Dict[str, Any]] = Field(default=None, description="Thresholds used for evaluation")

    # Locked constraints (Phase 1 fix)
    locked_constraints: List[str] = Field(default_factory=list, description="Constraints locked by user (no auto-adjust)")

    @property
    def needs_attention(self) -> bool:
        """Check if gate needs user attention"""
        return not self.passed and self.action == GateAction.HUMAN_INTERVENTION

    @property
    def is_critical(self) -> bool:
        """Check if gate failure is critical"""
        critical_failures = [f for f in self.failures if f.severity == ViolationSeverity.CRITICAL]
        return len(critical_failures) > 0

    class Config:
        json_schema_extra = {
            "example": {
                "gate_id": "admet_filters",
                "gate_name": "ADMET Screening",
                "passed": False,
                "molecules_evaluated": 1000,
                "molecules_passed": 85,
                "molecules_failed": 915,
                "pass_rate": 0.085,
                "primary_failure_type": "toxicity",
                "failure_breakdown": {
                    "toxicity": 650,
                    "hepatotoxicity": 265
                },
                "action": "adjust_parameters",
                "can_auto_adjust": True,
                "adjustment_suggestions": [
                    "Relax toxicity threshold from 0.5 to 0.55 (+10%)",
                    "Relax hepatotoxicity threshold from 0.3 to 0.32 (+5%)"
                ],
                "execution_time_seconds": 2.3
            }
        }


class QualityGateEvaluation(BaseModel):
    """
    Container for evaluating multiple quality gates

    Used in workflow_engine.py when evaluating all gates for a phase
    """
    phase: str = Field(..., description="Phase being evaluated")
    iteration_number: int = Field(..., ge=0, description="Iteration number")
    campaign_id: str = Field(..., description="Campaign identifier")

    # Gate results
    gate_results: List[QualityGateResult] = Field(..., description="Results from all gates")

    # Summary
    total_gates: int = Field(..., ge=0, description="Total number of gates")
    gates_passed: int = Field(..., ge=0, description="Number of gates passed")
    gates_failed: int = Field(..., ge=0, description="Number of gates failed")
    overall_passed: bool = Field(..., description="Whether all gates passed")

    # Timing
    total_evaluation_time_seconds: float = Field(default=0.0, ge=0.0, description="Total evaluation time")

    @property
    def critical_failures(self) -> List[QualityGateResult]:
        """Get gates with critical failures"""
        return [g for g in self.gate_results if g.is_critical]

    @property
    def requires_human_intervention(self) -> bool:
        """Check if any gate requires human intervention"""
        return any(g.needs_attention for g in self.gate_results)

    @property
    def can_auto_adjust(self) -> bool:
        """Check if failed gates can be auto-adjusted"""
        failed_gates = [g for g in self.gate_results if not g.passed]
        if not failed_gates:
            return False
        return all(g.can_auto_adjust for g in failed_gates)

    class Config:
        json_schema_extra = {
            "example": {
                "phase": "ADMET_SCREENING",
                "iteration_number": 5,
                "campaign_id": "campaign-abc-123",
                "gate_results": [
                    {
                        "gate_id": "admet_filters",
                        "gate_name": "ADMET Screening",
                        "passed": False,
                        "pass_rate": 0.085
                    }
                ],
                "total_gates": 1,
                "gates_passed": 0,
                "gates_failed": 1,
                "overall_passed": False,
                "total_evaluation_time_seconds": 2.3
            }
        }
