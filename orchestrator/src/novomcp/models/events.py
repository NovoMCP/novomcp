"""
Event Models for Event-Driven Architecture

Phase 2: Pydantic models for SQS events.
Prepares for transition from synchronous HTTP to async message-based communication.

Event Schema Versioning:
- v1: Initial implementation (Phase 2, Week 5)
- Future versions maintain backward compatibility via Optional fields
"""

from typing import Optional, Dict, Any, List, Literal
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Types of events in the system"""
    # Molecule lifecycle
    MOLECULE_GENERATED = "molecule.generated"
    MOLECULE_UPDATED = "molecule.updated"

    # Phase completions
    ADMET_COMPLETED = "admet.completed"
    DOCKING_COMPLETED = "docking.completed"
    MD_COMPLETED = "md.completed"
    OPTIMIZATION_COMPLETED = "optimization.completed"
    COMPLIANCE_COMPLETED = "compliance.completed"

    # Workflow state
    PHASE_STARTED = "phase.started"
    PHASE_COMPLETED = "phase.completed"
    ITERATION_COMPLETED = "iteration.completed"

    # Failures
    PHASE_FAILED = "phase.failed"
    SERVICE_FAILED = "service.failed"

    # Meta
    STUCK_DETECTED = "stuck.detected"  # Phase 3
    INTERVENTION_REQUIRED = "intervention.required"  # Phase 3


class BaseEvent(BaseModel):
    """
    Base event model for all SQS events

    All events inherit from this to ensure consistent structure
    """
    # Event metadata
    event_id: str = Field(..., description="Unique event identifier (UUID)")
    event_type: EventType = Field(..., description="Type of event")
    event_version: str = Field(default="v1", description="Event schema version")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Event creation time")

    # Correlation
    correlation_id: str = Field(..., description="Correlation ID for tracing across services")
    campaign_id: str = Field(..., description="Campaign identifier")
    iteration_number: int = Field(..., ge=0, description="Iteration number")

    # Source
    source_service: str = Field(..., description="Service that generated this event")

    # Retry metadata (for DLQ handling)
    retry_count: int = Field(default=0, ge=0, description="Number of retry attempts")
    max_retries: int = Field(default=3, ge=1, description="Maximum retry attempts")

    class Config:
        json_schema_extra = {
            "example": {
                "event_id": "evt-abc-123",
                "event_type": "molecule.generated",
                "event_version": "v1",
                "correlation_id": "req-456def",
                "campaign_id": "campaign-abc-123",
                "iteration_number": 5,
                "source_service": "molecular-intelligence"
            }
        }


class MoleculeGeneratedEvent(BaseEvent):
    """
    Event published when molecule(s) are generated

    Published by: molecular-intelligence service
    Consumed by: admet-consumer service
    """
    event_type: Literal[EventType.MOLECULE_GENERATED] = Field(default=EventType.MOLECULE_GENERATED)

    # Payload
    molecules: List[Dict[str, Any]] = Field(..., description="Generated molecules (batched)")
    batch_size: int = Field(..., ge=1, description="Number of molecules in this batch")
    batch_index: int = Field(..., ge=0, description="Batch index (for ordering)")

    # Generation metadata
    dataset: str = Field(..., description="Source dataset: 'drug-like', 'lead-like', etc.")
    constraints_used: Dict[str, Any] = Field(..., description="Constraints used for generation")

    class Config:
        json_schema_extra = {
            "example": {
                "event_type": "molecule.generated",
                "campaign_id": "campaign-abc-123",
                "molecules": [{"smiles": "CCO", "id": "mol-1"}],
                "batch_size": 10,
                "batch_index": 0,
                "dataset": "drug-like"
            }
        }


class ADMETCompletedEvent(BaseEvent):
    """
    Event published when ADMET screening completes

    Published by: admet-consumer service
    Consumed by: results-aggregator service
    """
    event_type: Literal[EventType.ADMET_COMPLETED] = Field(default=EventType.ADMET_COMPLETED)

    # Payload
    molecule_id: str = Field(..., description="Molecule identifier")
    passed: bool = Field(..., description="Whether molecule passed ADMET screening")

    # Results
    admet_properties: Optional[Dict[str, Any]] = Field(default=None, description="ADMET properties calculated")
    failures: List[str] = Field(default_factory=list, description="Failed ADMET criteria")

    # Thresholds used
    thresholds_used: Dict[str, float] = Field(..., description="Thresholds applied")

    class Config:
        json_schema_extra = {
            "example": {
                "event_type": "admet.completed",
                "molecule_id": "mol-1",
                "passed": True,
                "admet_properties": {
                    "toxicity": 0.15,
                    "hepatotoxicity": 0.08
                },
                "thresholds_used": {"toxicity": 0.5}
            }
        }


class DockingCompletedEvent(BaseEvent):
    """
    Event published when docking completes

    Published by: autodock-gpu service
    Consumed by: results-aggregator service
    """
    event_type: Literal[EventType.DOCKING_COMPLETED] = Field(default=EventType.DOCKING_COMPLETED)

    # Payload
    molecule_id: str = Field(..., description="Molecule identifier")
    passed: bool = Field(..., description="Whether docking passed threshold")

    # Results
    binding_affinity: float = Field(..., description="Binding affinity (kcal/mol)")
    binding_energy: float = Field(..., description="Binding energy (kcal/mol)")
    num_binding_modes: int = Field(default=1, ge=1, description="Number of binding modes")

    # Threshold
    threshold_used: float = Field(..., description="Binding affinity threshold (kcal/mol)")

    class Config:
        json_schema_extra = {
            "example": {
                "event_type": "docking.completed",
                "molecule_id": "mol-1",
                "passed": True,
                "binding_affinity": -8.5,
                "binding_energy": -9.2,
                "threshold_used": -7.0
            }
        }


class MDCompletedEvent(BaseEvent):
    """
    Event published when MD simulation completes

    Published by: gromacs-md service
    Consumed by: results-aggregator service
    """
    event_type: Literal[EventType.MD_COMPLETED] = Field(default=EventType.MD_COMPLETED)

    # Payload
    molecule_id: str = Field(..., description="Molecule identifier")
    passed: bool = Field(..., description="Whether MD simulation passed stability threshold")

    # Results
    rmsd: Optional[float] = Field(default=None, ge=0, description="RMSD (Å)")
    rmsf: Optional[float] = Field(default=None, ge=0, description="RMSF (Å)")
    stability_score: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="Overall stability score")

    # Simulation parameters
    simulation_time_ns: float = Field(..., ge=0, description="Simulation time (ns)")
    trajectory_s3_location: Optional[str] = Field(default=None, description="S3 location of trajectory")

    class Config:
        json_schema_extra = {
            "example": {
                "event_type": "md.completed",
                "molecule_id": "mol-1",
                "passed": True,
                "rmsd": 1.8,
                "stability_score": 0.92,
                "simulation_time_ns": 10.0
            }
        }


class OptimizationCompletedEvent(BaseEvent):
    """
    Event published when optimization completes

    Published by: molmim-optimizer or lead-optimization service
    Consumed by: results-aggregator service
    """
    event_type: Literal[EventType.OPTIMIZATION_COMPLETED] = Field(default=EventType.OPTIMIZATION_COMPLETED)

    # Payload
    original_molecule_id: str = Field(..., description="Original molecule identifier")
    optimized_molecule_id: str = Field(..., description="Optimized molecule identifier")
    optimized_smiles: str = Field(..., description="Optimized SMILES")

    # Results
    improvement_score: float = Field(..., ge=0.0, le=1.0, description="Optimization improvement (0-1)")
    modifications_made: List[str] = Field(..., description="List of modifications made")

    # Objectives
    objectives_achieved: Dict[str, float] = Field(..., description="Objective scores achieved")

    class Config:
        json_schema_extra = {
            "example": {
                "event_type": "optimization.completed",
                "original_molecule_id": "mol-1",
                "optimized_molecule_id": "mol-1-opt",
                "optimized_smiles": "CC(C)O",
                "improvement_score": 0.15,
                "modifications_made": ["added_methyl_group"],
                "objectives_achieved": {"binding_affinity": 0.85, "admet": 0.78}
            }
        }


class PhaseCompletedEvent(BaseEvent):
    """
    Event published when entire phase completes

    Published by: results-aggregator service
    Consumed by: workflow engine (novomcp)
    """
    event_type: Literal[EventType.PHASE_COMPLETED] = Field(default=EventType.PHASE_COMPLETED)

    # Payload
    phase: str = Field(..., description="Phase name (RETRIEVAL, ADMET_SCREENING, etc.)")
    status: str = Field(..., description="Phase status: 'completed', 'failed'")

    # Metrics
    input_count: int = Field(..., ge=0, description="Molecules input to phase")
    output_count: int = Field(..., ge=0, description="Molecules passing phase")
    pass_rate: float = Field(..., ge=0.0, le=1.0, description="Pass rate")

    # Quality gates
    gates_evaluated: List[str] = Field(..., description="Quality gates evaluated")
    gates_passed: List[str] = Field(..., description="Quality gates passed")
    gates_failed: List[str] = Field(..., description="Quality gates failed")

    # Timing
    duration_seconds: float = Field(..., ge=0, description="Phase duration")

    class Config:
        json_schema_extra = {
            "example": {
                "event_type": "phase.completed",
                "phase": "ADMET_SCREENING",
                "status": "completed",
                "input_count": 1000,
                "output_count": 85,
                "pass_rate": 0.085,
                "gates_evaluated": ["admet_filters"],
                "gates_passed": [],
                "gates_failed": ["admet_filters"],
                "duration_seconds": 12.5
            }
        }


class StuckDetectedEvent(BaseEvent):
    """
    Event published when campaign is stuck (Phase 3)

    Published by: meta-optimizer service
    Consumed by: intervention service
    """
    event_type: Literal[EventType.STUCK_DETECTED] = Field(default=EventType.STUCK_DETECTED)

    # Payload
    stuck_pattern: Dict[str, Any] = Field(..., description="Detected stuck pattern")
    consecutive_failures: int = Field(..., ge=3, description="Number of consecutive failures")
    failing_gate: str = Field(..., description="Gate that keeps failing")

    # Recommendations
    ai_recommendation: Optional[str] = Field(default=None, description="AI-generated recommendation")
    suggested_action: str = Field(..., description="Suggested action: 'adjust', 'regenerate', 'intervention'")

    # Similar campaigns
    similar_campaigns: List[str] = Field(default_factory=list, description="Similar campaign IDs from Pinecone")

    class Config:
        json_schema_extra = {
            "example": {
                "event_type": "stuck.detected",
                "stuck_pattern": {"gate": "admet_filters", "failure_type": "toxicity"},
                "consecutive_failures": 5,
                "failing_gate": "admet_filters",
                "suggested_action": "intervention",
                "ai_recommendation": "Switch to lead-like dataset - toxicity constraints too strict for drug-like"
            }
        }


class ServiceFailedEvent(BaseEvent):
    """
    Event published when service fails

    Published by: any service
    Consumed by: monitoring service, results-aggregator
    """
    event_type: Literal[EventType.SERVICE_FAILED] = Field(default=EventType.SERVICE_FAILED)

    # Failure details
    service_name: str = Field(..., description="Service that failed")
    error_type: str = Field(..., description="Error type (TimeoutError, HTTPStatusError, etc.)")
    error_message: str = Field(..., description="Error message")

    # Request info
    endpoint: str = Field(..., description="Endpoint that failed")
    request_payload: Optional[Dict[str, Any]] = Field(default=None, description="Request payload (for debugging)")

    # Stack trace
    stack_trace: Optional[str] = Field(default=None, description="Stack trace (if available)")

    class Config:
        json_schema_extra = {
            "example": {
                "event_type": "service.failed",
                "service_name": "autodock-gpu",
                "error_type": "TimeoutError",
                "error_message": "Request timed out after 300s",
                "endpoint": "/dock"
            }
        }


# Event wrapper for SQS messages
class SQSEventMessage(BaseModel):
    """
    Wrapper for SQS message body

    SQS messages contain this structure in the body field
    """
    event: BaseEvent = Field(..., description="The actual event")

    # SQS metadata
    message_id: Optional[str] = Field(default=None, description="SQS message ID")
    receipt_handle: Optional[str] = Field(default=None, description="SQS receipt handle (for deletion)")
    sent_timestamp: Optional[datetime] = Field(default=None, description="When message was sent to queue")

    @classmethod
    def from_sqs_message(cls, sqs_message: Dict[str, Any]) -> 'SQSEventMessage':
        """
        Parse SQS message into SQSEventMessage

        Usage:
            sqs_message = sqs.receive_message(QueueUrl=queue_url)
            event_msg = SQSEventMessage.from_sqs_message(sqs_message['Messages'][0])
        """
        import json

        body = json.loads(sqs_message['Body'])
        return cls(
            event=BaseEvent(**body),  # Parse specific event type
            message_id=sqs_message.get('MessageId'),
            receipt_handle=sqs_message.get('ReceiptHandle'),
            sent_timestamp=datetime.fromtimestamp(int(sqs_message.get('Attributes', {}).get('SentTimestamp', 0)) / 1000)
        )

    class Config:
        json_schema_extra = {
            "example": {
                "event": {
                    "event_type": "molecule.generated",
                    "campaign_id": "campaign-abc-123"
                },
                "message_id": "msg-123",
                "receipt_handle": "handle-456"
            }
        }
