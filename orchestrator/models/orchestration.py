"""
Orchestration Models

Core Pydantic models for the main orchestration API endpoints.
Replaces Dict[str, Any] with type-safe, validated models.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, validator


class PhaseStatus(str, Enum):
    """Phase execution status"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class OrchestrationRequest(BaseModel):
    """
    Request model for /ai/orchestrate endpoint

    Replaces: Dict[str, Any] in ai_orchestration.py line 173
    """
    campaign_id: str = Field(..., description="Unique campaign identifier")
    action: str = Field(..., description="Action to perform: 'start', 'continue', 'pause', 'resume', 'stop'")

    # Optional parameters
    parameters: Optional[Dict[str, Any]] = Field(default=None, description="Action-specific parameters")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Additional context for decision making")
    max_iterations: Optional[int] = Field(default=None, ge=1, le=100, description="Maximum iterations to run")
    autonomous: Optional[bool] = Field(default=True, description="Enable autonomous parameter adjustment")

    # Configuration overrides
    constraints: Optional[Dict[str, Any]] = Field(default=None, description="Molecular constraints override")
    thresholds: Optional[Dict[str, Any]] = Field(default=None, description="Quality gate thresholds override")

    @validator('action')
    def validate_action(cls, v):
        """Validate action is one of allowed values"""
        allowed_actions = {'start', 'continue', 'pause', 'resume', 'stop', 'adjust'}
        if v not in allowed_actions:
            raise ValueError(f"Action must be one of: {allowed_actions}")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "campaign_id": "campaign-abc-123",
                "action": "start",
                "max_iterations": 10,
                "autonomous": True,
                "parameters": {
                    "target_molecule_count": 1000
                }
            }
        }


class PhaseResult(BaseModel):
    """
    Result from a single phase execution

    Replaces: Dict[str, Any] phase results in workflow_engine.py
    """
    phase: str = Field(..., description="Phase name (RETRIEVAL, ADMET_SCREENING, etc.)")
    status: PhaseStatus = Field(..., description="Phase execution status")

    # Metrics
    input_count: int = Field(default=0, ge=0, description="Number of molecules input to phase")
    output_count: int = Field(default=0, ge=0, description="Number of molecules passing phase")
    pass_rate: float = Field(default=0.0, ge=0.0, le=1.0, description="Pass rate (0.0-1.0)")
    duration_seconds: float = Field(default=0.0, ge=0.0, description="Phase execution time")

    # Results
    results: Optional[Dict[str, Any]] = Field(default=None, description="Phase-specific results")
    failures: Optional[List[Dict[str, Any]]] = Field(default=None, description="Failure details if failed")

    # Literature context (for AI-enriched phases)
    literature_context: Optional[Dict[str, Any]] = Field(default=None, description="Relevant literature findings")

    # Warnings/notices
    warnings: List[str] = Field(default_factory=list, description="Non-fatal warnings")

    @property
    def passed(self) -> bool:
        """Check if phase passed"""
        return self.status == PhaseStatus.COMPLETED

    class Config:
        json_schema_extra = {
            "example": {
                "phase": "RETRIEVAL",
                "status": "completed",
                "input_count": 0,
                "output_count": 1000,
                "pass_rate": 1.0,
                "duration_seconds": 12.5,
                "results": {"molecules_generated": 1000}
            }
        }


class WorkflowState(BaseModel):
    """
    Current state of the workflow execution

    Replaces: Dict[str, Any] workflow state tracking
    """
    campaign_id: str = Field(..., description="Campaign identifier")
    iteration_number: int = Field(..., ge=0, description="Current iteration number")

    # Phase tracking
    current_phase: str = Field(..., description="Currently executing phase")
    phase_iteration: int = Field(default=1, ge=1, description="Iteration within current phase")
    total_phases: int = Field(default=5, ge=1, description="Total number of phases")

    # Molecule pipeline
    molecules_in_pipeline: int = Field(default=0, ge=0, description="Molecules currently being processed")
    molecules_completed: int = Field(default=0, ge=0, description="Molecules that completed all phases")
    molecules_discovered: int = Field(default=0, ge=0, description="High-quality discoveries")

    # Execution tracking
    started_at: Optional[datetime] = Field(default=None, description="Workflow start time")
    updated_at: Optional[datetime] = Field(default=None, description="Last update time")

    # Phase results history
    phase_results: List[PhaseResult] = Field(default_factory=list, description="Results from completed phases")

    # Status flags
    is_stuck: bool = Field(default=False, description="Stuck detection flag (Phase 3)")
    requires_intervention: bool = Field(default=False, description="Human intervention required")

    @property
    def progress_percentage(self) -> float:
        """Calculate overall progress percentage"""
        if self.total_phases == 0:
            return 0.0
        phase_index = self.total_phases - self.total_phases  # Simplified for now
        return min(100.0, (phase_index / self.total_phases) * 100.0)

    class Config:
        json_schema_extra = {
            "example": {
                "campaign_id": "campaign-abc-123",
                "iteration_number": 5,
                "current_phase": "ADMET_SCREENING",
                "phase_iteration": 1,
                "total_phases": 5,
                "molecules_in_pipeline": 850,
                "molecules_completed": 150,
                "molecules_discovered": 12
            }
        }


class OrchestrationResponse(BaseModel):
    """
    Response model for /ai/orchestrate endpoint

    Replaces: Dict[str, Any] returns in ai_orchestration.py
    """
    status: str = Field(..., description="Response status: 'success', 'error', 'partial'")
    message: str = Field(..., description="Human-readable status message")

    # Campaign info
    campaign_id: str = Field(..., description="Campaign identifier")
    iteration_number: int = Field(..., ge=0, description="Iteration number")

    # Workflow state
    workflow_state: Optional[WorkflowState] = Field(default=None, description="Current workflow state")

    # Results summary
    results: Optional[Dict[str, Any]] = Field(default=None, description="Execution results")
    metrics: Optional[Dict[str, Any]] = Field(default=None, description="Performance metrics")

    # Discoveries
    discoveries: List[Dict[str, Any]] = Field(default_factory=list, description="Molecules discovered this iteration")
    total_discoveries: int = Field(default=0, ge=0, description="Total discoveries across all iterations")

    # Actions taken
    actions_taken: List[str] = Field(default_factory=list, description="Actions performed (e.g., 'parameter_adjusted')")
    adjustments_made: Optional[Dict[str, Any]] = Field(default=None, description="Parameter adjustments (if any)")

    # Flags
    loop_back_triggered: bool = Field(default=False, description="Whether loop-back was triggered")
    early_exit_triggered: bool = Field(default=False, description="Whether early exit was triggered")

    # Next steps
    next_action: Optional[str] = Field(default=None, description="Recommended next action")
    warnings: List[str] = Field(default_factory=list, description="Warnings for user attention")
    errors: List[str] = Field(default_factory=list, description="Non-fatal errors encountered")

    # Timing
    execution_time_seconds: float = Field(default=0.0, ge=0.0, description="Total execution time")

    class Config:
        json_schema_extra = {
            "example": {
                "status": "success",
                "message": "Iteration 5 completed successfully",
                "campaign_id": "campaign-abc-123",
                "iteration_number": 5,
                "workflow_state": {
                    "current_phase": "COMPLETED",
                    "molecules_discovered": 12
                },
                "total_discoveries": 12,
                "actions_taken": ["parameter_adjusted", "quality_gates_evaluated"],
                "execution_time_seconds": 45.2
            }
        }
