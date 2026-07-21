"""
Internal Campaign Loop - Autonomous AI Operation
Runs campaigns directly within NovoMCP using internal function calls.
No HTTP overhead - pure Python async execution.
"""

import asyncio
import logging
import json
import uuid
import os
from datetime import datetime
from typing import Optional, Dict, Any
from ai.message_formatter import MessageFormatter

logger = logging.getLogger(__name__)

# Global campaign loop manager
active_campaigns: Dict[str, asyncio.Task] = {}


class CampaignLoopManager:
    """
    Manages autonomous campaign execution loops.
    Runs campaigns as background asyncio tasks within NovoMCP.
    """

    def __init__(self, campaign_decision_engine, get_campaign_status_func, learn_func, orchestrate_func):
        """
        Initialize with references to internal NovoMCP functions.

        Args:
            campaign_decision_engine: CampaignDecisionEngine instance
            get_campaign_status_func: async function to get campaign status
            learn_func: async function to store learning data
            orchestrate_func: async function to execute decisions
        """
        self.decision_engine = campaign_decision_engine
        self.get_campaign_status = get_campaign_status_func
        self.learn = learn_func
        self.orchestrate = orchestrate_func
        self.check_interval = 60  # 1 minute between iterations (reduced from 300s for faster throughput)

        # Track iteration numbers for each campaign
        self.campaign_iterations: Dict[str, int] = {}
        self.iteration_ids: Dict[str, str] = {}  # Track current iteration_id for updates

    async def run_campaign(self, campaign_id: str):
        """
        Run a single campaign autonomously using phase-based workflow.
        This runs as a background task within NovoMCP.
        """
        logger.info(f"Starting autonomous campaign loop: {campaign_id}")

        # CRITICAL FIX: Recover iteration number from database on startup
        # Prevents iteration reset on service restart
        if campaign_id not in self.campaign_iterations:
            recovered_iteration = await self._recover_iteration_number(campaign_id)
            self.campaign_iterations[campaign_id] = recovered_iteration
            logger.info(f"Recovered iteration number for campaign {campaign_id}: {recovered_iteration}")

        while True:
            try:
                # 1. Get campaign state (internal function call)
                campaign = await self._get_campaign_safe(campaign_id)
                if not campaign or campaign.get('status') != 'active':
                    logger.info(f"Campaign {campaign_id} is not active, stopping loop")
                    break

                # 2. Check safety limits before starting iteration
                should_halt, halt_reason = await self._check_safety_limits(campaign_id, campaign)
                if should_halt:
                    logger.warning(f"Campaign {campaign_id} halted: {halt_reason}")
                    await self._broadcast_iteration_event('campaign_halted', campaign_id, {
                        'reason': halt_reason,
                        'iteration_count': self.campaign_iterations.get(campaign_id, 0)
                    })
                    break

                # 3. Start iteration tracking (persist to Research DB)
                iteration_id = await self._start_iteration(campaign_id, campaign)

                # Get current iteration number
                iteration_number = self.campaign_iterations.get(campaign_id, 1)

                # 4. Initialize WorkflowEngine with campaign config and azure_client for AI decisions
                from .workflow_engine import WorkflowEngine, PhaseAction

                campaign_config = {
                    "id": campaign_id,
                    "campaign_id": campaign_id,
                    "goal": campaign.get("goal"),
                    "constraints": campaign.get("constraints"),
                    "dataSources": campaign.get("dataSources"),
                    "autonomy": campaign.get("autonomy"),
                    "workflow_state": campaign.get("workflow_state"),
                    "target_protein": campaign.get("target_protein"),
                    "iteration_number": iteration_number,
                    "quantum_enabled": campaign.get("quantum_enabled", False)
                }

                # Pass azure_client from decision_engine for AI-powered configuration and decision logging
                azure_client = self.decision_engine.azure_client if self.decision_engine else None
                # CRITICAL FIX: Pass iteration_id to WorkflowEngine for indexed database updates
                workflow_engine = WorkflowEngine(campaign_config, azure_client, iteration_id)
                current_phase = workflow_engine.state.current_phase.value

                # Broadcast iteration_started event via WebSocket
                await self._broadcast_iteration_event('iteration_started', campaign_id, {
                    'iteration_number': iteration_number,
                    'iteration_id': iteration_id,
                    'phase': current_phase
                })

                # 5. Execute current workflow phase (Generation → ADMET → Compliance → Validation → Optimization)
                logger.info(f"Campaign {campaign_id} executing phase: {current_phase}")
                phase_result = await workflow_engine.execute_current_phase(self.orchestrate)

                # 6. Evaluate quality gates
                gate_evaluation = await workflow_engine.evaluate_phase_gate(phase_result)
                action = gate_evaluation.get('action')
                next_phase = gate_evaluation.get('next_phase')

                # 7. Handle quality gate outcome
                if action == PhaseAction.PROCEED:
                    # Quality gate passed, proceed to next phase
                    await workflow_engine.transition_phase(action, next_phase, "Quality gate passed")
                    outcome_status = 'completed'
                    outcome_reason = f"Phase {current_phase} completed, proceeding to {next_phase.value if next_phase else 'next phase'}"

                    # CRITICAL: Persist workflow state so molecules_in_pipeline survives to next iteration
                    campaign_config['workflow_state'] = workflow_engine.state.to_dict()
                    logger.info(f"[STATE_PERSIST] Saved workflow state: phase={workflow_engine.state.current_phase.value}, molecules={len(workflow_engine.state.molecules_in_pipeline)}")

                elif action == PhaseAction.LOOP_BACK:
                    # Quality gate failed, loop back with adjusted parameters
                    adjustments = gate_evaluation.get('parameter_adjustments', {})
                    loop_back_reason = MessageFormatter.format_loop_back_message(adjustments)

                    await workflow_engine.transition_phase(action, next_phase, loop_back_reason)
                    await self._broadcast_iteration_event('campaign_loop_back', campaign_id, {
                        'iteration_number': iteration_number,
                        'reason': loop_back_reason,
                        'from_phase': current_phase,
                        'to_phase': next_phase.value if next_phase else 'unknown',
                        'adjustments': adjustments
                    })

                    outcome_status = 'failed'
                    outcome_reason = loop_back_reason

                    # CRITICAL: Persist workflow state even on loop_back so state survives to next iteration
                    campaign_config['workflow_state'] = workflow_engine.state.to_dict()
                    logger.info(f"[STATE_PERSIST] Saved workflow state after loop_back: phase={workflow_engine.state.current_phase.value}, molecules={len(workflow_engine.state.molecules_in_pipeline)}")

                    # CRITICAL FIX: Persist parameter adjustments so they apply to next iteration
                    # Copy updated config values (thresholds, docking, constraints) back to campaign_config
                    if workflow_engine.config.get('thresholds'):
                        campaign_config['thresholds'] = workflow_engine.config['thresholds']
                        logger.info(f"[CONFIG_PERSIST] Saved adjusted thresholds: {campaign_config['thresholds']}")
                    if workflow_engine.config.get('docking'):
                        campaign_config['docking'] = workflow_engine.config['docking']
                        logger.info(f"[CONFIG_PERSIST] Saved adjusted docking config: {campaign_config['docking']}")
                    if workflow_engine.config.get('constraints'):
                        campaign_config['constraints'] = workflow_engine.config['constraints']
                        logger.info(f"[CONFIG_PERSIST] Saved adjusted constraints")
                    if workflow_engine.config.get('generation'):
                        campaign_config['generation'] = workflow_engine.config['generation']
                        logger.info(f"[CONFIG_PERSIST] Saved adjusted generation config")

                elif action == PhaseAction.HUMAN_INTERVENTION:
                    # Request human intervention
                    intervention_message = MessageFormatter.format_intervention_message(
                        gate_evaluation.get('gate_results', [])
                    )
                    await self._broadcast_iteration_event('intervention_required', campaign_id, {
                        'iteration_number': iteration_number,
                        'reason': intervention_message,
                        'severity': 'high',
                        'phase': current_phase,
                        'gate_results': gate_evaluation.get('gate_results', [])
                    })
                    outcome_status = 'failed'
                    outcome_reason = intervention_message
                    # Pause campaign
                    logger.warning(f"Campaign {campaign_id} requires human intervention, pausing...")
                    break

                elif action == PhaseAction.HALT:
                    # Circuit breaker open, halt campaign
                    await self._broadcast_iteration_event('campaign_halted', campaign_id, {
                        'reason': 'Circuit breaker open - too many failures',
                        'iteration_count': iteration_number
                    })
                    outcome_status = 'failed'
                    outcome_reason = 'Circuit breaker open'
                    break

                elif action == PhaseAction.DISCOVERY:
                    # Discovery made, campaign complete
                    logger.info(f"🎉 Campaign {campaign_id} reached DISCOVERY - submitting manuscript generation")

                    # Submit asynchronous manuscript generation job
                    try:
                        manuscript_job_id = await self.generate_manuscript_async(campaign_id)
                        logger.info(f"📄 Manuscript generation job {manuscript_job_id} submitted for campaign {campaign_id}")
                    except Exception as e:
                        logger.error(f"Failed to submit manuscript generation for campaign {campaign_id}: {e}")
                        # Non-critical failure - campaign completion continues

                    outcome_status = 'completed'
                    outcome_reason = 'Discovery made - campaign successful'
                    # Mark campaign as completed externally
                    break

                else:
                    # Unknown action
                    outcome_status = 'failed'
                    outcome_reason = f"Unknown quality gate action: {action}"

                # 8. Update workflow state in campaign (for persistence)
                campaign['workflow_state'] = workflow_engine.get_workflow_state()

                # 9. Complete iteration tracking with outcome
                metrics = {
                    'molecules_generated': len(phase_result.get('results', {}).get('molecules', [])),
                    'leads_discovered': len(workflow_engine.state.top_candidates)
                }
                await self._complete_iteration(campaign_id, outcome_status, outcome_reason, metrics)

                # Broadcast iteration_completed event via WebSocket
                await self._broadcast_iteration_event('iteration_completed', campaign_id, {
                    'iteration_number': iteration_number,
                    'outcome': outcome_status,
                    'outcome_reason': outcome_reason,
                    'metrics': metrics,
                    'phase': current_phase,
                    'next_phase': next_phase.value if next_phase else None
                })

                # 10. Learn from outcome (store workflow state and gate results)
                await self._learn_from_workflow(campaign_id, workflow_engine, gate_evaluation, phase_result)

                # 11. Sleep before next iteration (BATCHING FIX: skip sleep for fast phases)
                # Fast phases: retrieval, admet_screening, compliance (< 15 seconds each)
                # Slow phases: validation, optimization (may take minutes)
                fast_phases = ['retrieval', 'admet_screening', 'compliance']
                should_sleep = current_phase not in fast_phases

                if should_sleep:
                    logger.info(f"Campaign {campaign_id} completed iteration {iteration_number} (slow phase: {current_phase}), sleeping {self.check_interval}s")
                    await asyncio.sleep(self.check_interval)
                else:
                    logger.info(f"Campaign {campaign_id} completed iteration {iteration_number} (fast phase: {current_phase}), proceeding immediately to next phase")
                    await asyncio.sleep(5)  # Minimal 5s delay for event broadcasting

            except asyncio.CancelledError:
                logger.info(f"Campaign {campaign_id} loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in campaign loop {campaign_id}: {e}", exc_info=True)
                # Mark iteration as failed if we were tracking one
                try:
                    await self._complete_iteration(
                        campaign_id,
                        'failed',
                        f"Loop error: {str(e)}",
                        {'molecules_generated': 0, 'leads_discovered': 0}
                    )
                except:
                    pass
                await asyncio.sleep(self.check_interval)  # Retry after error

        logger.info(f"Campaign {campaign_id} loop stopped")

    async def _get_campaign_safe(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        """Get campaign with error handling"""
        try:
            return await self.get_campaign_status(campaign_id)
        except Exception as e:
            logger.error(f"Failed to get campaign {campaign_id}: {e}")
            return None

    async def _recover_iteration_number(self, campaign_id: str) -> int:
        """
        Recover iteration number from database on service restart.

        CRITICAL FIX: Prevents iteration counter reset when NovoMCP restarts.
        Queries MAX(iteration_number) from campaign_iterations table.

        Args:
            campaign_id: Campaign identifier

        Returns:
            Last iteration number from database, or 0 if no iterations found
        """
        try:
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from core.db_helper import query_sql

            result = await query_sql("""
                SELECT COALESCE(MAX(iteration_number), 0) as max_iteration
                FROM campaign_iterations
                WHERE campaign_id = %s
            """, (campaign_id,))

            if result and len(result) > 0:
                max_iteration = result[0].get('max_iteration', 0)
                logger.info(f"Campaign {campaign_id}: Recovered from database - last iteration was {max_iteration}")
                return max_iteration
            else:
                logger.info(f"Campaign {campaign_id}: No previous iterations found in database, starting from 0")
                return 0

        except Exception as e:
            logger.error(f"Failed to recover iteration number for campaign {campaign_id}: {e}", exc_info=True)
            # On error, default to 0 (new campaign) rather than crashing
            logger.warning(f"Campaign {campaign_id}: Defaulting to iteration 0 due to recovery error")
            return 0

    async def _make_decision(self, campaign: Dict[str, Any]) -> Dict[str, Any]:
        """Make autonomous decision using the decision engine"""
        try:
            campaign_id = campaign.get('campaign_id') or campaign.get('id')
            metrics = campaign.get('metrics', {})
            constraints = campaign.get('constraints', {})

            # Calculate success rate and timeline/budget
            molecules_generated = metrics.get('molecules_generated', 0)
            successful_leads = metrics.get('successful_leads', 0)
            success_rate = successful_leads / molecules_generated if molecules_generated > 0 else 0.0

            # Parse created_at with error handling
            timeline_days = constraints.get('timeline_days', 90)
            try:
                created_at_str = campaign.get('created_at')
                if isinstance(created_at_str, str):
                    # Handle ISO format with or without timezone
                    created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                    days_elapsed = (datetime.utcnow() - created_at).days
                    timeline_remaining = max(0, timeline_days - days_elapsed)
                else:
                    # If created_at is missing or invalid, assume campaign just started
                    logger.warning(f"Campaign {campaign_id} missing valid created_at, using full timeline")
                    timeline_remaining = timeline_days
            except Exception as e:
                logger.warning(f"Failed to parse created_at for {campaign_id}: {e}, using full timeline")
                timeline_remaining = timeline_days

            # Build comprehensive decision context
            context = {
                "campaign_id": campaign_id,
                "goal": campaign.get('goal'),
                "molecules_generated": molecules_generated,
                "successful_leads": successful_leads,
                "experiments_run": metrics.get('experiments_run', 0),
                "failure_count": metrics.get('failure_count', 0),
                "success_rate": success_rate,
                "timeline_remaining": timeline_remaining,
                "budget_remaining": constraints.get('budget', 100000),
                "constraints": constraints,
                "dataSources": campaign.get('dataSources', {}),
                "recent_results": campaign.get('recent_decisions', [])
            }

            # Call decision engine directly
            decision = await self.decision_engine.make_autonomous_decision(context)
            logger.info(f"Campaign {campaign_id} decision: {decision.get('action')}")
            return decision

        except Exception as e:
            logger.error(f"Error making decision: {e}", exc_info=True)
            return {"action": "wait", "reasoning": f"Decision error: {str(e)}"}

    async def _execute_decision(self, campaign_id: str, decision: Dict[str, Any], campaign: Dict[str, Any]) -> Dict[str, Any]:
        """Execute decision through internal orchestration"""
        action = decision.get('action', 'wait')

        if action == 'wait':
            return {"status": "waiting", "message": "No action needed"}

        try:
            # Get current iteration number
            iteration_number = self.campaign_iterations.get(campaign_id, 1)

            # Build comprehensive context for workflow engine
            context = {
                **decision.get('context', {}),
                "goal": campaign.get('goal'),
                "constraints": campaign.get('constraints'),
                "dataSources": campaign.get('dataSources'),
                "autonomy": campaign.get('autonomy'),
                "workflow_state": campaign.get('workflow_state'),
                "circuit_breaker_state": campaign.get('circuit_breaker_state'),
                "target_protein": campaign.get('target_protein'),
                "use_workflow_engine": True,  # Enable workflow engine
                "iteration_number": iteration_number  # Pass iteration number to WorkflowEngine
            }

            # Call orchestrate function directly (internal)
            result = await self.orchestrate(
                campaign_id=campaign_id,
                action=action,
                parameters=decision.get('parameters', {}),
                context=context
            )

            # Defensive: normalize unexpected orchestrate return types
            if not isinstance(result, dict):
                logger.error(f"Orchestrate returned unexpected type: {type(result).__name__}")
                result = {
                    "status": "error",
                    "message": f"Orchestrate returned unexpected type: {type(result).__name__}",
                    "raw_result": str(result)
                }

            # Store decision in SQL (non-blocking)
            await self._store_decision_to_sql(campaign_id, decision, result)

            return result
        except Exception as e:
            logger.error(f"Error executing decision for {campaign_id}: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}

    async def _store_decision_to_sql(
        self,
        campaign_id: str,
        decision: Dict[str, Any],
        result: Dict[str, Any]
    ) -> None:
        """Store campaign decision in SQL database"""
        try:
            # Import db helper (lazy import to avoid circular dependencies)
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from core.db_helper import execute_sql

            # Prepare decision data
            decision_id = str(uuid.uuid4())
            decision_type = decision.get('action', 'unknown')
            reasoning = decision.get('reasoning', '')
            input_context = json.dumps(decision.get('parameters', {}))
            outcome = json.dumps(result)
            success_score = 1.0 if result.get('status') == 'success' else 0.0

            # Get current iteration number for logging
            iteration_number = self.campaign_iterations.get(campaign_id, 1)

            # Insert into campaign_decisions table (Research DB)
            # Schema: id, campaign_id, timestamp, decision_type, reasoning, input_context, outcome, success_score
            await execute_sql("""
                INSERT INTO campaign_decisions (
                    id, campaign_id, timestamp, decision_type, reasoning, input_context,
                    outcome, success_score
                ) VALUES (%s, %s, GETUTCDATE(), %s, %s, %s, %s, %s)
            """, (
                decision_id,
                campaign_id,
                decision_type,
                reasoning,
                input_context,
                outcome,
                success_score
            ))

            logger.info(f"Stored decision to SQL: {campaign_id} - {decision_type} (iteration {iteration_number})")

        except Exception as e:
            # Non-critical - log but don't fail campaign loop
            logger.error(f"Failed to store decision to SQL: {e}", exc_info=True)

    async def _learn_from_outcome(
        self,
        campaign_id: str,
        decision: Dict[str, Any],
        result: Dict[str, Any],
        campaign: Dict[str, Any]
    ):
        """Store learning data and patterns to Research DB (DEPRECATED - kept for backward compatibility)"""
        try:
            # Get current iteration number
            iteration_number = self.campaign_iterations.get(campaign_id, 1)

            # Call decision engine's learn_from_outcome to update weights and store patterns
            await self.decision_engine.learn_from_outcome(
                decision=decision,
                outcome=result,
                campaign_id=campaign_id,
                iteration_number=iteration_number
            )

            # Also call external learn function if provided (for backward compatibility)
            if self.learn:
                await self.learn(
                    campaign_id=campaign_id,
                    decision=decision,
                    outcome=result,
                    context={
                        "goal": campaign.get('goal', ''),
                        "constraints": campaign.get('constraints', {})
                    }
                )
        except Exception as e:
            logger.debug(f"Error recording learning (non-critical): {e}")

    async def _learn_from_workflow(
        self,
        campaign_id: str,
        workflow_engine,
        gate_evaluation: Dict[str, Any],
        phase_result: Dict[str, Any]
    ):
        """
        Store workflow state, quality gate results, and learning patterns to Research DB.

        Args:
            campaign_id: Campaign identifier
            workflow_engine: WorkflowEngine instance with current state
            gate_evaluation: Quality gate evaluation results
            phase_result: Phase execution results
        """
        try:
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from core.db_helper import execute_sql

            # Get current iteration number
            iteration_number = self.campaign_iterations.get(campaign_id, 1)

            # 1. Store workflow state AND adjusted constraints for next iteration
            workflow_state = workflow_engine.get_workflow_state()
            workflow_state_json = json.dumps(workflow_state)

            # CRITICAL: Also persist adjusted constraints from adaptive parameter system
            # workflow_engine.config contains relaxed constraints after adjust_parameters_on_failure
            adjusted_constraints = workflow_engine.config.get('constraints', {})
            adjusted_constraints_json = json.dumps(adjusted_constraints)

            # Update campaign with workflow state AND adjusted constraints (for persistence)
            await execute_sql("""
                UPDATE campaigns
                SET workflow_state = %s,
                    constraints = %s,
                    updated_at = GETUTCDATE()
                WHERE id = %s
            """, (workflow_state_json, adjusted_constraints_json, campaign_id))

            logger.info(f"Stored workflow state and adjusted constraints for campaign {campaign_id} (phase: {workflow_state.get('current_phase')})")

            # 2. Store learning patterns if quality gate failed (for adaptive learning)
            if not gate_evaluation.get('passed'):
                adjustments = gate_evaluation.get('parameter_adjustments', {})

                # SQL storage: Only store to SQL if adjustments were actually made
                if adjustments.get('changes'):
                    import hashlib

                    pattern_id = str(uuid.uuid4())
                    context_data = {
                        "campaign_id": str(campaign_id),  # Ensure UUID is converted to string
                        "iteration_number": iteration_number,
                        "adjustments": adjustments
                    }
                    context_json = json.dumps(context_data)

                    # Generate pattern hash for deduplication
                    pattern_hash = hashlib.sha256(
                        json.dumps(adjustments.get('changes', []), sort_keys=True).encode()
                    ).hexdigest()

                    # UPSERT: Update if pattern_hash exists, insert if new
                    await execute_sql("""
                        IF EXISTS (SELECT 1 FROM learning_patterns WHERE pattern_hash = %s)
                        BEGIN
                            UPDATE learning_patterns
                            SET occurrence_count = occurrence_count + 1,
                                last_seen = GETUTCDATE(),
                                context = %s
                            WHERE pattern_hash = %s
                        END
                        ELSE
                        BEGIN
                            INSERT INTO learning_patterns (
                                id, pattern_hash, pattern_type, success_rate,
                                occurrence_count, last_seen, context
                            ) VALUES (%s, %s, 'parameter_adjustment', 0.5, 1, GETUTCDATE(), %s)
                        END
                    """, (
                        pattern_hash,  # Check if exists
                        context_json,  # Update context
                        pattern_hash,  # WHERE clause
                        pattern_id,    # INSERT id
                        pattern_hash,  # INSERT pattern_hash
                        context_json   # INSERT context
                    ))

                    logger.info(f"Stored learning pattern for campaign {campaign_id}: {len(adjustments.get('changes', []))} parameter adjustments")

                # CRITICAL FIX: ALWAYS store to Pinecone for ALL gate failures (even with locked constraints!)
                # This captures valuable learning: "locked constraints + this goal = failure"
                try:
                    from core.pinecone_client import get_pinecone_client
                    pinecone_client = get_pinecone_client()

                    # Build context data with lock information
                    molecules_count = len(phase_result.get('results', {}).get('molecules', []))
                    context_data = {
                        "campaign_id": str(campaign_id),
                        "iteration_number": iteration_number,
                        "gate_name": gate_evaluation.get('gate_name', 'unknown'),
                        "adjustments_made": len(adjustments.get('changes', [])),
                        "locked_constraints": adjustments.get('locked_constraints', []),
                        "molecules_returned": molecules_count,
                        "constraints": adjusted_constraints
                    }

                    # Build decision and outcome for Pinecone storage
                    decision = {
                        "action": "gate_failure" if adjustments.get('changes') else "gate_failure_locked",
                        "reasoning": f"Quality gate {gate_evaluation.get('gate_name', 'unknown')} failed: {gate_evaluation.get('message', '')}",
                        "confidence": 0.3 if adjustments.get('changes') else 0.1,  # Lower confidence for locked failures
                        "parameters": adjustments,
                        "locked": adjustments.get('locked_constraints', [])
                    }
                    outcome = {
                        "status": "failed",
                        "message": gate_evaluation.get('message', 'Quality gate failed'),
                        "success": False,
                        "molecules_count": molecules_count,
                        "adjustments_possible": bool(adjustments.get('changes'))
                    }

                    await pinecone_client.store_learning_pattern(
                        campaign_id=str(campaign_id),
                        decision=decision,
                        outcome=outcome,
                        context=context_data
                    )
                    logger.info(f"Stored learning pattern to Pinecone for campaign {campaign_id} (adjustments: {len(adjustments.get('changes', []))}, locked: {len(adjustments.get('locked_constraints', []))})")
                except Exception as pinecone_error:
                    # Don't fail workflow if Pinecone fails, but log the error
                    logger.error(f"Failed to store learning pattern to Pinecone: {str(pinecone_error)}", exc_info=True)

            # 3. Store significant discoveries from phase result
            molecules = phase_result.get('results', {}).get('molecules', [])
            if molecules:
                top_molecules = sorted(
                    molecules,
                    key=lambda m: m.get('composite_score', 0),
                    reverse=True
                )[:5]  # Store top 5 per iteration

                for molecule in top_molecules:
                    significance = molecule.get('composite_score', 0)
                    if significance > 0.7:  # Only store significant discoveries
                        await self._store_discovery(
                            campaign_id=campaign_id,
                            molecule_data=molecule,
                            significance=significance,
                            discovery_type='lead' if significance > 0.9 else 'hit'
                        )

        except Exception as e:
            logger.error(f"Error storing workflow learning data: {e}", exc_info=True)

    async def _start_iteration(
        self,
        campaign_id: str,
        campaign: Dict[str, Any]
    ) -> str:
        """
        Start a new iteration and persist to Research DB.
        Returns iteration_id for tracking updates.
        """
        try:
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from core.db_helper import execute_sql

            # Increment iteration number for this campaign
            if campaign_id not in self.campaign_iterations:
                self.campaign_iterations[campaign_id] = 0
            self.campaign_iterations[campaign_id] += 1
            iteration_number = self.campaign_iterations[campaign_id]

            # Generate iteration_id
            iteration_id = str(uuid.uuid4())
            self.iteration_ids[campaign_id] = iteration_id

            # Extract parameters from campaign (phase-based workflow, no decision)
            generation_params = json.dumps(campaign.get('constraints', {}).get('retrieval', campaign.get('constraints', {}).get('generation', {})))
            quality_thresholds = json.dumps(campaign.get('constraints', {}).get('quality_thresholds', {}))

            # Insert into campaign_iterations table (Research DB)
            await execute_sql("""
                INSERT INTO campaign_iterations (
                    iteration_id, campaign_id, iteration_number,
                    started_at, status,
                    generation_params, quality_thresholds
                ) VALUES (%s, %s, %s, GETUTCDATE(), 'running', %s, %s)
            """, (
                iteration_id,
                campaign_id,
                iteration_number,
                generation_params,
                quality_thresholds
            ))

            logger.info(f"Started iteration {iteration_number} for campaign {campaign_id} (iteration_id: {iteration_id})")
            return iteration_id

        except Exception as e:
            logger.error(f"Failed to start iteration tracking: {e}", exc_info=True)
            # Return a fallback iteration_id so execution can continue
            fallback_id = str(uuid.uuid4())
            self.iteration_ids[campaign_id] = fallback_id
            return fallback_id

    async def _complete_iteration(
        self,
        campaign_id: str,
        outcome: str,
        outcome_reason: str,
        metrics: Dict[str, Any]
    ) -> None:
        """
        Complete current iteration and update final metrics in Research DB.

        Args:
            campaign_id: Campaign identifier
            outcome: 'completed' or 'failed' (CHECK constraint values)
            outcome_reason: Human-readable reason for outcome
            metrics: Final metrics (molecules_generated, leads_discovered, etc.)
        """
        try:
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from core.db_helper import execute_sql

            iteration_id = self.iteration_ids.get(campaign_id)
            if not iteration_id:
                logger.warning(f"No iteration_id found for campaign {campaign_id}, skipping completion")
                return

            # Update iteration record (WorkflowEngine populates phase metrics)
            await execute_sql("""
                UPDATE campaign_iterations
                SET completed_at = GETUTCDATE(),
                    status = %s,
                    loop_back_triggered = %s,
                    loop_back_reason = %s
                WHERE iteration_id = %s
            """, (
                outcome,  # 'success', 'partial', or 'failed'
                1 if outcome == 'failed' else 0,
                outcome_reason if outcome == 'failed' else None,
                iteration_id
            ))

            iteration_number = self.campaign_iterations.get(campaign_id, 0)
            logger.info(f"Completed iteration {iteration_number} for campaign {campaign_id}: {outcome}")

        except Exception as e:
            logger.error(f"Failed to complete iteration tracking: {e}", exc_info=True)

    async def _store_discovery(
        self,
        campaign_id: str,
        molecule_data: Dict[str, Any],
        significance: float,
        discovery_type: str = 'lead'
    ) -> None:
        """
        Store a significant discovery (lead candidate) to Research DB.

        Args:
            campaign_id: Campaign identifier
            molecule_data: Molecule details (smiles, scores, properties)
            significance: Significance score (0.0-1.0)
            discovery_type: 'lead', 'hit', or 'breakthrough'
        """
        try:
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from core.db_helper import execute_sql

            discovery_id = str(uuid.uuid4())
            iteration_number = self.campaign_iterations.get(campaign_id, 1)

            # Extract molecule details
            smiles = molecule_data.get('smiles', '')
            binding_affinity = molecule_data.get('binding_affinity')
            quantum_score = molecule_data.get('quantum_score')
            admet_score = molecule_data.get('admet_score')
            properties = json.dumps(molecule_data.get('properties', {}))
            validation_results = json.dumps(molecule_data.get('validation_results', {}))

            # Insert discovery
            await execute_sql("""
                INSERT INTO campaign_discoveries (
                    discovery_id, campaign_id, iteration_number, discovered_at,
                    smiles, significance, discovery_type,
                    binding_affinity, quantum_score, admet_score,
                    properties, validation_results
                ) VALUES (%s, %s, %s, GETUTCDATE(), %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                discovery_id,
                campaign_id,
                iteration_number,
                smiles,
                significance,
                discovery_type,
                binding_affinity,
                quantum_score,
                admet_score,
                properties,
                validation_results
            ))

            logger.info(f"Stored {discovery_type} discovery for campaign {campaign_id} (iteration {iteration_number}): {smiles[:50]}...")

        except Exception as e:
            logger.error(f"Failed to store discovery: {e}", exc_info=True)

    async def _broadcast_iteration_event(
        self,
        event_type: str,
        campaign_id: str,
        event_data: Dict[str, Any]
    ) -> None:
        """
        Broadcast iteration events via WebSocket to all connected clients.

        Args:
            event_type: 'iteration_started', 'iteration_completed', or 'campaign_loop_back'
            campaign_id: Campaign identifier
            event_data: Event-specific data
        """
        try:
            # Import broadcast function (lazy import to avoid circular dependencies)
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from routers.ai_orchestration import broadcast_global_update

            # Build event payload
            payload = {
                'campaign_id': campaign_id,
                'timestamp': datetime.utcnow().isoformat(),
                **event_data
            }

            # Broadcast via Redis pub/sub → WebSocket
            await broadcast_global_update(event_type, payload)

            logger.info(f"Broadcasted {event_type} for campaign {campaign_id} (iteration {event_data.get('iteration_number')})")

        except Exception as e:
            # Non-critical - log but don't fail campaign loop
            logger.error(f"Failed to broadcast iteration event: {e}", exc_info=True)

    async def generate_manuscript_async(self, campaign_id: str) -> str:
        """
        Submit manuscript generation job to SQS via ai_orchestration endpoint.

        Args:
            campaign_id: UUID of the campaign

        Returns:
            job_id: UUID of the manuscript generation job
        """
        import httpx
        import uuid
        import json
        import boto3
        import redis.asyncio as redis
        from datetime import datetime

        try:
            job_id = str(uuid.uuid4())

            # Send directly to SQS (same pattern as ai_orchestration.py)
            sqs = boto3.client('sqs', region_name='us-east-1')
            queue_response = sqs.get_queue_url(QueueName='novomcp-molecular-jobs')

            # Build job message
            message_body = {
                "job_id": job_id,
                "job_type": "manuscript",
                "campaign_id": campaign_id,
                "parameters": {
                    "campaign_id": campaign_id,
                    "manuscript_type": "auto"
                }
            }

            # Send to SQS
            sqs.send_message(
                QueueUrl=queue_response['QueueUrl'],
                MessageBody=json.dumps(message_body)
            )

            # Initialize Redis tracking
            redis_url = os.getenv('REDIS_URL', 'redis://novomcp-redis-cluster.qkrqx3.ng.0001.use1.cache.amazonaws.com:6379')
            redis_client = redis.from_url(redis_url, decode_responses=True)

            redis_key = f"novomcp:job:{job_id}"
            await redis_client.hset(
                redis_key,
                mapping={
                    "status": "queued",
                    "job_id": job_id,
                    "job_type": "manuscript",
                    "campaign_id": campaign_id,
                    "submitted_at": datetime.utcnow().isoformat()
                }
            )
            await redis_client.expire(redis_key, 3600)  # 1 hour TTL
            await redis_client.close()

            logger.info(f"📄 Manuscript generation job {job_id} submitted for campaign {campaign_id}")
            return job_id

        except Exception as e:
            logger.error(f"Failed to submit manuscript generation job: {e}", exc_info=True)
            raise

    async def _check_safety_limits(
        self,
        campaign_id: str,
        campaign: Dict[str, Any]
    ) -> tuple[bool, str]:
        """
        Check safety limits to prevent runaway autonomous operations.

        Args:
            campaign_id: Campaign identifier
            campaign: Campaign data with constraints

        Returns:
            Tuple of (should_halt: bool, halt_reason: str)
        """
        try:
            constraints = campaign.get('constraints', {})

            # 1. Check max iterations limit
            max_iterations = constraints.get('max_iterations')
            if max_iterations:
                current_iteration = self.campaign_iterations.get(campaign_id, 0)
                if current_iteration >= max_iterations:
                    return (True, f"Max iterations limit reached ({current_iteration}/{max_iterations})")

            # 2. Check budget limit
            budget_limit_usd = constraints.get('budget_limit_usd')
            if budget_limit_usd:
                # Query cumulative spend from Research DB
                try:
                    import sys
                    import os
                    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    from core.db_helper import query_sql

                    # Calculate cumulative cost from iterations
                    result = await query_sql("""
                        SELECT COALESCE(SUM(
                            (phase_1_input * 0.10) +  -- $0.10 per molecule generated
                            (phase_4_output * 1.00)   -- $1.00 per final lead
                        ), 0) as total_cost
                        FROM campaign_iterations
                        WHERE campaign_id = %s AND status = 'completed'
                    """, (campaign_id,))

                    if result and len(result) > 0:
                        total_cost = result[0].get('total_cost', 0)
                        if total_cost >= budget_limit_usd:
                            return (True, f"Budget limit exceeded (${total_cost:.2f}/${budget_limit_usd:.2f})")
                except Exception as e:
                    logger.error(f"Failed to check budget limit: {e}")
                    # Continue checking other limits even if budget check fails

            # 3. Check runtime limit
            max_runtime_hours = constraints.get('max_runtime_hours')
            if max_runtime_hours:
                try:
                    created_at_str = campaign.get('created_at')
                    if isinstance(created_at_str, str):
                        created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                        hours_elapsed = (datetime.utcnow() - created_at).total_seconds() / 3600
                        if hours_elapsed >= max_runtime_hours:
                            return (True, f"Runtime limit exceeded ({hours_elapsed:.1f}h/{max_runtime_hours}h)")
                except Exception as e:
                    logger.error(f"Failed to check runtime limit: {e}")

            # 4. Check circuit breaker state
            circuit_breaker_state = campaign.get('circuit_breaker_state', 'closed')
            if circuit_breaker_state in ('open', 'half-open'):
                # Query recent failure rate from Research DB
                try:
                    import sys
                    import os
                    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                    from core.db_helper import query_sql

                    # Check last 5 iterations for failure rate
                    result = await query_sql("""
                        SELECT
                            COUNT(*) as total,
                            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failures
                        FROM campaign_iterations
                        WHERE campaign_id = %s
                        AND status IN ('completed', 'failed')
                        ORDER BY iteration_number DESC
                        LIMIT 5
                    """, (campaign_id,))

                    if result and len(result) > 0:
                        total = result[0].get('total', 0)
                        failures = result[0].get('failures', 0)

                        if total >= 3:  # Need at least 3 iterations to assess
                            failure_rate = failures / total

                            # If circuit breaker is open and failure rate is still high, halt
                            if circuit_breaker_state == 'open' and failure_rate >= 0.8:
                                return (True, f"Circuit breaker OPEN - high failure rate ({failure_rate:.0%})")

                            # If half-open, allow one more attempt but monitor closely
                            if circuit_breaker_state == 'half-open':
                                logger.warning(f"Circuit breaker HALF-OPEN for {campaign_id} - monitoring next iteration")
                                # Don't halt - give it one more chance
                except Exception as e:
                    logger.error(f"Failed to check circuit breaker: {e}")

            # All safety checks passed
            return (False, "")

        except Exception as e:
            logger.error(f"Error checking safety limits: {e}", exc_info=True)
            # On error, err on the side of caution but don't halt
            # (let the campaign continue but log the error)
            return (False, "")

    def start_campaign(self, campaign_id: str) -> bool:
        """Start a campaign loop as a background task"""
        if campaign_id in active_campaigns and not active_campaigns[campaign_id].done():
            logger.warning(f"Campaign {campaign_id} is already running")
            return False

        # Create and store the task
        task = asyncio.create_task(self.run_campaign(campaign_id))

        # Add callback to log when task completes
        def task_done_callback(t):
            try:
                if t.exception():
                    logger.error(f"Campaign {campaign_id} task failed: {t.exception()}")
                else:
                    logger.info(f"Campaign {campaign_id} task completed normally")
            except Exception as e:
                logger.error(f"Error in task callback for {campaign_id}: {e}")

        task.add_done_callback(task_done_callback)
        active_campaigns[campaign_id] = task

        logger.info(f"Started campaign loop for {campaign_id}")
        return True

    def stop_campaign(self, campaign_id: str) -> bool:
        """Stop a running campaign loop"""
        if campaign_id not in active_campaigns:
            logger.warning(f"Campaign {campaign_id} is not running")
            return False

        task = active_campaigns[campaign_id]
        if not task.done():
            task.cancel()

        del active_campaigns[campaign_id]
        logger.info(f"Stopped campaign loop for {campaign_id}")
        return True

    def get_running_campaigns(self) -> list:
        """Get list of currently running campaigns"""
        running = []
        for campaign_id, task in list(active_campaigns.items()):
            if not task.done():
                running.append(campaign_id)
            else:
                # Clean up completed tasks
                del active_campaigns[campaign_id]
        return running


# Global instance (initialized in main.py)
campaign_loop_manager: Optional[CampaignLoopManager] = None


def get_campaign_loop_manager() -> Optional[CampaignLoopManager]:
    """Get the global campaign loop manager instance"""
    return campaign_loop_manager


def initialize_campaign_loop_manager(
    decision_engine,
    get_status_func,
    learn_func,
    orchestrate_func
) -> CampaignLoopManager:
    """Initialize the global campaign loop manager"""
    global campaign_loop_manager
    campaign_loop_manager = CampaignLoopManager(
        decision_engine,
        get_status_func,
        learn_func,
        orchestrate_func
    )
    logger.info("Campaign loop manager initialized")
    return campaign_loop_manager
