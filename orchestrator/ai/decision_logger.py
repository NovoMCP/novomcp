"""
Decision Logger for Audit Trail and Compliance
Logs high-level scientific decisions without revealing tool/service implementation details
"""

import json
import logging
import uuid
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class DecisionLogger:
    """
    Logs campaign decisions with compliance-safe scientific reasoning.

    Decision summaries focus on:
    - Scientific rationale and strategy
    - Literature-informed parameter choices
    - What changed (molecular constraints, strategy) but not how
    - NO tool/service names (DrugSynthMC, AutoDock, etc.)
    - NO technical implementation details
    """

    def __init__(self, azure_client, campaign_id: str):
        self.azure_client = azure_client
        self.campaign_id = campaign_id

    async def log_campaign_initialization(
        self,
        wizard_config: Dict[str, Any],
        literature_context: Dict[str, Any],
        generation_strategy: Dict[str, Any]
    ) -> str:
        """
        Log initial campaign configuration decision.

        Args:
            wizard_config: User inputs from Launch Discovery Campaign wizard
            literature_context: Pinecone literature search results
            generation_strategy: Configured generation parameters

        Returns:
            decision_id
        """
        try:
            # Generate AI reasoning summary
            reasoning = await self._generate_initialization_reasoning(
                wizard_config,
                literature_context,
                generation_strategy
            )

            # Store decision
            decision_id = await self._store_decision(
                decision_type="initialize_retrieval",
                reasoning=reasoning,
                input_context={
                    "goal": wizard_config.get("goal"),
                    "therapeutic_area": wizard_config.get("dataSources", {}).get("therapeuticArea"),
                    "molecular_constraints": wizard_config.get("constraints", {}).get("molecular", {}),
                    "literature_papers_analyzed": literature_context.get("total_results", 0)
                },
                parameters=self._sanitize_parameters(generation_strategy),
                success_score=1.0
            )

            logger.info(f"Logged campaign initialization for {self.campaign_id}")
            return decision_id

        except Exception as e:
            logger.error(f"Failed to log campaign initialization: {e}", exc_info=True)
            return str(uuid.uuid4())

    async def log_phase_completion(
        self,
        phase: str,
        metrics: Dict[str, Any],
        gate_passed: bool
    ) -> str:
        """
        Log phase completion with scientific summary.

        Args:
            phase: Workflow phase name
            metrics: Phase execution metrics
            gate_passed: Whether quality gate passed

        Returns:
            decision_id
        """
        try:
            reasoning = await self._generate_phase_summary(phase, metrics, gate_passed)

            decision_id = await self._store_decision(
                decision_type=f"{phase}_{'complete' if gate_passed else 'failed'}",
                reasoning=reasoning,
                input_context={
                    "phase": phase,
                    "molecules_evaluated": metrics.get("molecules_evaluated", 0),
                    "molecules_passed": metrics.get("molecules_passed", 0)
                },
                parameters={},
                success_score=1.0 if gate_passed else 0.0
            )

            logger.info(f"Logged {phase} completion for {self.campaign_id}")
            return decision_id

        except Exception as e:
            logger.error(f"Failed to log phase completion: {e}", exc_info=True)
            return str(uuid.uuid4())

    async def log_gate_failure_reconfiguration(
        self,
        phase: str,
        gate_failures: list,
        literature_context: Dict[str, Any],
        parameter_adjustments: Dict[str, Any]
    ) -> str:
        """
        Log quality gate failure and reconfiguration decision.

        Args:
            phase: Failed phase name
            gate_failures: List of gate evaluation failures
            literature_context: Pinecone literature/pattern search results
            parameter_adjustments: New generation parameters

        Returns:
            decision_id
        """
        try:
            reasoning = await self._generate_failure_reasoning(
                phase,
                gate_failures,
                literature_context,
                parameter_adjustments
            )

            decision_id = await self._store_decision(
                decision_type=f"{phase}_failure_reconfiguration",
                reasoning=reasoning,
                input_context={
                    "phase": phase,
                    "failure_types": [f.get("failure_type") for f in gate_failures],
                    "literature_papers_consulted": literature_context.get("total_results", 0),
                    "adjustments_made": len(parameter_adjustments.get("changes", []))
                },
                parameters=parameter_adjustments,  # Store full adjustments, not sanitized
                success_score=0.5  # Changed from 0.0 to 0.5 (50% confidence for reconfigurations)
            )

            logger.info(f"Logged gate failure reconfiguration for {self.campaign_id}")
            return decision_id

        except Exception as e:
            logger.error(f"Failed to log gate failure: {e}", exc_info=True)
            return str(uuid.uuid4())

    async def log_therapeutic_discovery(
        self,
        final_candidates: list,
        discovery_summary: Dict[str, Any]
    ) -> str:
        """
        Log successful therapeutic discovery.

        Args:
            final_candidates: List of discovered therapeutic leads
            discovery_summary: Summary metrics

        Returns:
            decision_id
        """
        try:
            reasoning = await self._generate_discovery_summary(
                final_candidates,
                discovery_summary
            )

            decision_id = await self._store_decision(
                decision_type="therapeutic_discovery_complete",
                reasoning=reasoning,
                input_context={
                    "candidates_discovered": len(final_candidates),
                    "avg_composite_score": discovery_summary.get("avg_score", 0)
                },
                parameters={},
                success_score=1.0
            )

            logger.info(f"Logged therapeutic discovery for {self.campaign_id}")
            return decision_id

        except Exception as e:
            logger.error(f"Failed to log discovery: {e}", exc_info=True)
            return str(uuid.uuid4())

    async def _generate_initialization_reasoning(
        self,
        wizard_config: Dict[str, Any],
        literature_context: Dict[str, Any],
        generation_strategy: Dict[str, Any]
    ) -> str:
        """Generate AI reasoning for campaign initialization"""
        try:
            goal = wizard_config.get("goal", "")
            therapeutic_area = wizard_config.get("dataSources", {}).get("therapeuticArea", "")
            constraints = wizard_config.get("constraints", {}).get("molecular", {})

            # Get literature insights
            top_insights = literature_context.get("top_insights", [])
            insights_text = "\n".join([
                f"- {ins['title']} (relevance: {ins['relevance_score']:.2f})"
                for ins in top_insights[:3]
            ]) if top_insights else "No direct literature matches found"

            prompt = f"""You are summarizing the initial configuration of a drug discovery campaign for audit and compliance purposes.

Campaign Goal: {goal}
Therapeutic Area: {therapeutic_area}

Molecular Constraints (from wizard Step 2 - Lipinski Rules):
- Molecular Weight: {constraints.get('mw', {}).get('min', 0)}-{constraints.get('mw', {}).get('max', 500)} Da
- LogP: {constraints.get('logp', {}).get('min', 0)}-{constraints.get('logp', {}).get('max', 5)}
- Hydrogen Bond Donors: max {constraints.get('hbd', {}).get('max', 5)}
- Hydrogen Bond Acceptors: max {constraints.get('hba', {}).get('max', 10)}

Literature Analysis ({literature_context.get('total_results', 0)} relevant papers):
{insights_text}

Configured Strategy:
- Generation approach: {generation_strategy.get('strategy', 'diverse_exploration')}
- MCTS algorithm: {generation_strategy.get('algorithm', 'PUCT')}
- Target count: {generation_strategy.get('count', 1000)} candidates
- Diversity: {generation_strategy.get('diversity', 0.5)}
- Novelty: {generation_strategy.get('novelty', 0.5)}

Write a 2-3 sentence compliance-safe summary explaining:
1. The scientific strategy chosen based on literature
2. Key molecular constraints applied
3. Generation approach (diverse exploration vs focused optimization)

IMPORTANT: Do NOT mention specific tools, software, or services (DrugSynthMC, AutoDock, etc.).
Focus on HIGH-LEVEL SCIENTIFIC REASONING only."""

            response = await self.azure_client.complete(
                prompt=prompt,
                system_prompt="You are a scientific writer creating audit trail documentation for drug discovery campaigns. Be concise, professional, and compliance-focused.",
                temperature=0.3,
                max_tokens=200
            )

            if response.get("success"):
                return response.get("response", "Campaign initialized with literature-informed molecular generation strategy.")
            else:
                return self._fallback_initialization_reasoning(wizard_config, literature_context)

        except Exception as e:
            logger.error(f"Failed to generate initialization reasoning: {e}")
            return self._fallback_initialization_reasoning(wizard_config, literature_context)

    async def _generate_phase_summary(
        self,
        phase: str,
        metrics: Dict[str, Any],
        gate_passed: bool
    ) -> str:
        """Generate concise phase completion summary"""
        try:
            molecules_in = metrics.get("molecules_evaluated", 0)
            molecules_out = metrics.get("molecules_passed", 0)
            pass_rate = metrics.get("pass_rate", 0)

            # Map technical phase names to scientific descriptions
            phase_descriptions = {
                "retrieval": "molecular candidate retrieval",
                "admet_screening": "pharmacokinetic and safety screening",
                "compliance": "regulatory compliance and knowledge validation",
                "validation": "target binding and stability validation",
                "optimization": "lead optimization and refinement"
            }

            phase_desc = phase_descriptions.get(phase, phase)

            if gate_passed:
                # Handle edge case: 0 molecules retrieved
                if molecules_in == 0:
                    return f"Completed {phase_desc}. WARNING: No molecular candidates were retrieved. Query service may have failed or returned empty results."
                return f"Completed {phase_desc}. {molecules_in} candidates evaluated, {molecules_out} advanced to next stage (pass rate: {pass_rate:.0%}). Candidates demonstrate favorable properties within target constraints."
            else:
                failure_reasons = []
                if "admet" in phase.lower():
                    failure_reasons.append("pharmacokinetic properties")
                elif "binding" in phase.lower() or "validation" in phase.lower():
                    failure_reasons.append("target affinity")
                elif "compliance" in phase.lower():
                    failure_reasons.append("regulatory filters")
                else:
                    failure_reasons.append("quality thresholds")

                return f"Completed {phase_desc}. {molecules_in} candidates evaluated, {molecules_out} met quality criteria. Insufficient candidates passed {', '.join(failure_reasons)} requirements. Initiating reconfiguration."

        except Exception as e:
            logger.error(f"Failed to generate phase summary: {e}")
            return f"Completed {phase} phase with {metrics.get('molecules_passed', 0)} candidates advancing."

    async def _generate_failure_reasoning(
        self,
        phase: str,
        gate_failures: list,
        literature_context: Dict[str, Any],
        parameter_adjustments: Dict[str, Any]
    ) -> str:
        """Generate AI reasoning for gate failure and reconfiguration"""
        try:
            # Analyze failure patterns
            failure_types = [f.get("failure_type", "unknown") for f in gate_failures]
            failure_summary = ", ".join(set(failure_types))

            # Get adjustments
            changes = parameter_adjustments.get("changes", [])
            adjustment_summary = "\n".join([
                f"- {c.get('parameter')}: {c.get('old_value')} → {c.get('new_value')} ({c.get('reason')})"
                for c in changes[:3]
            ]) if changes else "Parameter optimization based on failure analysis"

            # Get literature context
            top_insights = literature_context.get("top_insights", [])
            insights_text = "\n".join([
                f"- {ins['title']}"
                for ins in top_insights[:2]
            ]) if top_insights else "No direct literature guidance available"

            prompt = f"""You are summarizing a quality gate failure and reconfiguration decision for a drug discovery campaign audit trail.

Phase Failed: {phase}
Failure Types: {failure_summary}

Literature Consulted ({literature_context.get('total_results', 0)} papers):
{insights_text}

Parameter Adjustments Made:
{adjustment_summary}

Write a 2-3 sentence compliance-safe summary explaining:
1. What quality criteria failed
2. What literature or patterns informed the reconfiguration
3. What strategy was adjusted (molecular constraints, diversity, selectivity)

IMPORTANT: Do NOT mention tools/services. Focus on SCIENTIFIC STRATEGY CHANGES only."""

            response = await self.azure_client.complete(
                prompt=prompt,
                system_prompt="You are a scientific writer creating audit trail documentation. Be concise and compliance-focused.",
                temperature=0.3,
                max_tokens=200
            )

            if response.get("success"):
                return response.get("response", f"{phase} screening identified issues with {failure_summary}. Reconfiguring generation parameters based on literature analysis.")
            else:
                return self._fallback_failure_reasoning(phase, failure_types, changes)

        except Exception as e:
            logger.error(f"Failed to generate failure reasoning: {e}")
            return self._fallback_failure_reasoning(phase, gate_failures, parameter_adjustments.get("changes", []))

    async def _generate_discovery_summary(
        self,
        final_candidates: list,
        discovery_summary: Dict[str, Any]
    ) -> str:
        """Generate therapeutic discovery summary"""
        try:
            count = len(final_candidates)
            avg_score = discovery_summary.get("avg_score", 0)

            # Extract key properties
            avg_mw = sum(c.get("properties", {}).get("mw", 0) for c in final_candidates) / count if count > 0 else 0
            avg_logp = sum(c.get("properties", {}).get("logp", 0) for c in final_candidates) / count if count > 0 else 0

            return f"Therapeutic discovery complete: {count} lead candidates identified with composite quality score ≥{discovery_summary.get('threshold', 0.85):.2f} (average: {avg_score:.2f}). Candidates demonstrate favorable drug-like properties (avg MW: {avg_mw:.0f} Da, LogP: {avg_logp:.1f}) and passed all validation stages including target binding, pharmacokinetic screening, and stability assessment."

        except Exception as e:
            logger.error(f"Failed to generate discovery summary: {e}")
            return f"Therapeutic discovery complete: {len(final_candidates)} lead candidates identified."

    def _sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Remove tool-specific parameters and keep only high-level scientific constraints.

        Keeps:
        - Molecular constraints (MW, LogP, HBD, HBA)
        - Strategy descriptors (diversity, novelty, selectivity)
        - Scientific thresholds

        Removes:
        - Tool names (drugsynthmc_*, autodock_*)
        - Implementation details (queue names, API endpoints)
        - Service configurations
        """
        sanitized = {}

        # Whitelist of compliance-safe parameters
        safe_keys = [
            "strategy", "diversity", "novelty", "selectivity",
            "mw", "logp", "hbd", "hba", "tpsa", "rotatable_bonds",
            "binding_threshold", "admet_threshold", "quantum_threshold",
            "count", "algorithm"  # High-level only
        ]

        for key, value in params.items():
            # Keep whitelisted keys
            if any(safe_key in key.lower() for safe_key in safe_keys):
                sanitized[key] = value
            # Keep constraint dictionaries
            elif key == "constraints" and isinstance(value, dict):
                sanitized[key] = self._sanitize_parameters(value)

        return sanitized

    def _fallback_initialization_reasoning(
        self,
        wizard_config: Dict[str, Any],
        literature_context: Dict[str, Any]
    ) -> str:
        """Fallback reasoning when AI fails"""
        goal = wizard_config.get("goal", "therapeutic discovery")
        papers = literature_context.get("total_results", 0)
        constraints = wizard_config.get("constraints", {}).get("molecular", {})

        return f"Initialized {goal} campaign with literature-informed molecular generation strategy ({papers} relevant papers analyzed). Configured generation with Lipinski-compliant constraints (MW: {constraints.get('mw', {}).get('max', 500)} Da, LogP: {constraints.get('logp', {}).get('max', 5)}) for drug-like candidate exploration."

    def _fallback_failure_reasoning(
        self,
        phase: str,
        failure_types: list,
        changes: list
    ) -> str:
        """Fallback reasoning for failures"""
        failures = ", ".join(set(failure_types)) if failure_types else "quality thresholds"
        adjustments = len(changes)

        return f"{phase.title()} screening identified issues with {failures}. Adjusted {adjustments} molecular generation parameters to optimize for safer, more selective candidates. Loop-back to generation phase initiated."

    async def _store_decision(
        self,
        decision_type: str,
        reasoning: str,
        input_context: Dict[str, Any],
        parameters: Dict[str, Any],
        success_score: float
    ) -> str:
        """Store decision to campaign_decisions table"""
        try:
            from core.db_helper import execute_sql

            decision_id = str(uuid.uuid4())

            await execute_sql("""
                INSERT INTO campaign_decisions (
                    id, campaign_id, timestamp, decision_type, reasoning,
                    input_context, outcome, success_score
                ) VALUES (%s, %s, GETUTCDATE(), %s, %s, %s, %s, %s)
            """, (
                decision_id,
                self.campaign_id,
                decision_type,
                reasoning,
                json.dumps(input_context),
                json.dumps(parameters),
                success_score
            ))

            logger.info(f"Stored decision {decision_type} for campaign {self.campaign_id}")
            return decision_id

        except Exception as e:
            logger.error(f"Failed to store decision: {e}", exc_info=True)
            return str(uuid.uuid4())
