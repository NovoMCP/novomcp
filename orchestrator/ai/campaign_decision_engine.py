"""
Campaign Decision Engine for Autonomous Drug Discovery
Uses GPT-5 reasoning to make strategic decisions about campaign direction
"""

import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import asyncio
from enum import Enum

logger = logging.getLogger(__name__)

class DecisionType(Enum):
    """Types of autonomous decisions the engine can make"""
    GENERATE_NEW_MOLECULES = "generate_new_molecules"
    OPTIMIZE_EXISTING_LEADS = "optimize_existing_leads"
    PIVOT_STRATEGY = "pivot_strategy"
    SCREEN_COMPOUNDS = "screen_compounds"
    REQUEST_HUMAN_INPUT = "request_human_input"
    COMPLETE_MILESTONE = "complete_milestone"
    ADJUST_PARAMETERS = "adjust_parameters"
    EXPAND_CHEMICAL_SPACE = "expand_chemical_space"

class CampaignDecisionEngine:
    """
    Autonomous decision-making engine for long-running drug discovery campaigns.
    Uses GPT-5 to analyze campaign state and decide next actions.
    """

    def __init__(self, azure_client, db_manager=None):
        self.azure_client = azure_client
        self.db_manager = db_manager
        self.decision_weights = self._initialize_weights()

    def _initialize_weights(self) -> Dict[str, float]:
        """Initialize decision weights based on historical success patterns"""
        return {
            DecisionType.GENERATE_NEW_MOLECULES.value: 1.0,
            DecisionType.OPTIMIZE_EXISTING_LEADS.value: 1.2,
            DecisionType.PIVOT_STRATEGY.value: 0.8,
            DecisionType.SCREEN_COMPOUNDS.value: 1.1,
            DecisionType.REQUEST_HUMAN_INPUT.value: 0.3,
            DecisionType.COMPLETE_MILESTONE.value: 0.9,
            DecisionType.ADJUST_PARAMETERS.value: 0.7,
            DecisionType.EXPAND_CHEMICAL_SPACE.value: 0.6
        }

    async def make_autonomous_decision(self, campaign_context: dict) -> Dict[str, Any]:
        """
        Make an autonomous decision based on campaign state and goals.

        Args:
            campaign_context: Current state of the campaign including:
                - campaign_id: Unique identifier
                - goal: Campaign objective
                - molecules_generated: Count of molecules created
                - successful_leads: Promising candidates found
                - failure_count: Number of failed attempts
                - timeline_remaining: Days left in campaign
                - budget_remaining: Available resources
                - recent_results: Latest experimental outcomes
                - dataSources: Search preferences for Pinecone filtering

        Returns:
            Decision with action type, parameters, and reasoning
        """
        try:
            # QUERY PINECONE for relevant literature context
            # Campaigns query global Pinecone with their search preferences
            literature_context = await self._get_literature_context(campaign_context)

            # GET FAILURE PATTERNS for learning from past mistakes
            failure_analysis = await self._get_failure_analysis(campaign_context)

            # Enrich campaign context with literature insights and failure patterns
            enriched_context = {
                **campaign_context,
                'literature_insights': literature_context,
                'failure_analysis': failure_analysis
            }

            # Build decision prompt with enriched context
            prompt = self._build_decision_prompt(enriched_context)

            # Use GPT-5 for strategic reasoning
            system_message = """You are an expert drug discovery AI making autonomous strategic decisions.
            Analyze the campaign state and recommend the next best action to achieve the goal.
            Consider success rates, timeline, resources, and recent learnings.
            Your decision should balance exploration and exploitation."""

            response = await self.azure_client.complete(
                prompt=prompt,
                system_prompt=system_message,
                temperature=0.3,  # Lower temperature for more consistent decisions
                max_tokens=1000
            )

            if not response.get("success"):
                logger.error(f"GPT-5 decision failed: {response.get('error')}")
                return self._fallback_decision(campaign_context)

            # Parse and validate decision
            decision = self._parse_decision(response.get("response"), campaign_context)

            # Apply learned weights to influence decision confidence
            decision = self._apply_decision_weights(decision)

            # Store decision for learning
            if self.db_manager:
                await self._store_decision(campaign_context["campaign_id"], decision)

            logger.info(f"Autonomous decision made for campaign {campaign_context.get('campaign_id')}: {decision['action']}")

            return decision

        except Exception as e:
            logger.error(f"Decision engine error: {str(e)}")
            return self._fallback_decision(campaign_context)

    async def _get_literature_context(self, campaign_context: dict) -> Dict[str, Any]:
        """
        Query Pinecone for relevant literature based on campaign search preferences

        Uses campaign's searchKeywords, therapeuticArea, modality to filter global literature base
        """
        try:
            from core.pinecone_client import get_pinecone_client

            pinecone_client = get_pinecone_client()
            data_sources = campaign_context.get('dataSources', {})

            # Build query text from search preferences
            search_keywords = data_sources.get('searchKeywords', [])
            therapeutic_area = data_sources.get('therapeuticArea', '')
            modality = data_sources.get('modality', '')
            goal = campaign_context.get('goal', '')

            query_text = f"{goal} {therapeutic_area} {modality} {' '.join(search_keywords)}"

            logger.info(f"Querying Pinecone with: {query_text}")

            # Query Pinecone using search_literature (which handles embeddings internally)
            papers = await pinecone_client.search_literature(
                query=query_text,
                filters={
                    'therapeutic_area': therapeutic_area,
                    'modality': modality
                },
                top_k=10
            )

            # Extract insights from top results
            insights = []
            for paper in papers[:5]:
                insights.append({
                    'title': paper.get('title', 'Untitled'),
                    'source': paper.get('doi', 'Unknown'),
                    'relevance_score': paper.get('relevance', 0),
                    'summary': paper.get('abstract', '')[:200]
                })

            return {
                'total_results': len(papers),
                'top_insights': insights,
                'query_text': query_text
            }

        except Exception as e:
            logger.error(f"Failed to get literature context: {e}")
            return {
                'total_results': 0,
                'top_insights': [],
                'error': str(e)
            }

    async def _get_failure_analysis(self, campaign_context: dict) -> Dict[str, Any]:
        """
        Get failure pattern analysis for this campaign to learn from past mistakes.

        Queries Research DB for:
        - Top failure patterns from campaign_iterations
        - Phase-specific failures from quality_gate_evaluations
        - Cross-campaign learning patterns (successes and failures)
        """
        try:
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from core.db_helper import query_sql

            campaign_id = campaign_context.get('campaign_id')

            # 1. Get top 3 failure patterns for this campaign
            try:
                top_failures = await query_sql("""
                    SELECT TOP 3
                        outcome_reason,
                        COUNT(*) as occurrence_count
                    FROM campaign_iterations
                    WHERE campaign_id = %s AND outcome = 'failed'
                    GROUP BY outcome_reason
                    ORDER BY occurrence_count DESC
                """, (campaign_id,))
            except Exception:
                # Fallback for schemas without 'outcome' column
                top_failures = await query_sql("""
                    SELECT TOP 3
                        outcome_reason,
                        COUNT(*) as occurrence_count
                    FROM campaign_iterations
                    WHERE campaign_id = %s AND status = 'completed' AND outcome_reason IS NOT NULL
                    GROUP BY outcome_reason
                    ORDER BY occurrence_count DESC
                """, (campaign_id,))

            # 2. Get worst-performing phases
            phase_failures = await query_sql("""
                SELECT TOP 3
                    phase,
                    gate_id,
                    CAST(SUM(CASE WHEN passed = 0 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) as failure_rate
                FROM quality_gate_evaluations
                WHERE campaign_id = %s
                GROUP BY phase, gate_id
                HAVING COUNT(*) >= 3
                ORDER BY failure_rate DESC
            """, (campaign_id,))

            # 3. Get consistently failing patterns across all campaigns (to avoid)
            patterns_to_avoid = await query_sql("""
                SELECT TOP 3
                    pattern_type,
                    AVG(success_rate) as avg_success_rate,
                    COUNT(DISTINCT campaign_id) as campaigns_affected
                FROM learning_patterns
                WHERE success_rate < 0.3
                GROUP BY pattern_type
                HAVING COUNT(DISTINCT campaign_id) >= 2
                ORDER BY avg_success_rate ASC
            """, ())

            # 4. Get consistently successful patterns (to prefer)
            patterns_to_prefer = await query_sql("""
                SELECT TOP 3
                    pattern_type,
                    AVG(success_rate) as avg_success_rate,
                    SUM(occurrence_count) as total_occurrences
                FROM learning_patterns
                WHERE success_rate > 0.8
                GROUP BY pattern_type
                HAVING SUM(occurrence_count) >= 5
                ORDER BY avg_success_rate DESC
            """, ())

            return {
                'has_data': len(top_failures) > 0 or len(phase_failures) > 0,
                'top_failures': [
                    {
                        'reason': f.get('outcome_reason', 'Unknown'),
                        'count': f.get('occurrence_count', 0)
                    }
                    for f in (top_failures or [])
                ],
                'phase_failures': [
                    {
                        'phase': f.get('phase', 'Unknown'),
                        'gate': f.get('gate_id', 'Unknown'),
                        'failure_rate': f.get('failure_rate', 0)
                    }
                    for f in (phase_failures or [])
                ],
                'patterns_to_avoid': [
                    {
                        'pattern_type': p.get('pattern_type', 'Unknown'),
                        'success_rate': p.get('avg_success_rate', 0),
                        'campaigns_affected': p.get('campaigns_affected', 0)
                    }
                    for p in (patterns_to_avoid or [])
                ],
                'patterns_to_prefer': [
                    {
                        'pattern_type': p.get('pattern_type', 'Unknown'),
                        'success_rate': p.get('avg_success_rate', 0),
                        'occurrences': p.get('total_occurrences', 0)
                    }
                    for p in (patterns_to_prefer or [])
                ]
            }

        except Exception as e:
            logger.error(f"Failed to get failure analysis: {e}")
            return {
                'has_data': False,
                'top_failures': [],
                'phase_failures': [],
                'patterns_to_avoid': [],
                'patterns_to_prefer': [],
                'error': str(e)
            }

    def _build_decision_prompt(self, context: dict) -> str:
        """Build detailed prompt for GPT-5 decision-making"""
        literature_insights = context.get('literature_insights', {})
        top_insights = literature_insights.get('top_insights', [])

        insights_text = "\n".join([
            f"- {ins['title']} ({ins['source']}, relevance: {ins['relevance_score']:.2f})"
            for ins in top_insights[:3]
        ]) if top_insights else "No recent literature insights available"

        # Build failure analysis section
        failure_analysis = context.get('failure_analysis', {})
        has_failure_data = failure_analysis.get('has_data', False)

        failure_insights_text = ""
        if has_failure_data:
            # Top failures for this campaign
            top_failures = failure_analysis.get('top_failures', [])
            if top_failures:
                failure_insights_text += "\n\n        Historical Failures (This Campaign):\n"
                for failure in top_failures[:3]:
                    failure_insights_text += f"        - {failure['reason']} (occurred {failure['count']} times)\n"

            # Phase-specific failures
            phase_failures = failure_analysis.get('phase_failures', [])
            if phase_failures:
                failure_insights_text += "\n        Problem Areas by Phase:\n"
                for pf in phase_failures[:3]:
                    failure_insights_text += f"        - {pf['phase']} phase, {pf['gate']} gate: {pf['failure_rate']:.0%} failure rate\n"

            # Patterns to avoid (cross-campaign)
            patterns_to_avoid = failure_analysis.get('patterns_to_avoid', [])
            if patterns_to_avoid:
                failure_insights_text += "\n        Strategies to AVOID (Failed Across Multiple Campaigns):\n"
                for pattern in patterns_to_avoid[:3]:
                    failure_insights_text += f"        - {pattern['pattern_type']}: {pattern['success_rate']:.0%} success rate across {pattern['campaigns_affected']} campaigns\n"

            # Patterns to prefer (cross-campaign)
            patterns_to_prefer = failure_analysis.get('patterns_to_prefer', [])
            if patterns_to_prefer:
                failure_insights_text += "\n        Strategies to PREFER (Successful Across Campaigns):\n"
                for pattern in patterns_to_prefer[:3]:
                    failure_insights_text += f"        - {pattern['pattern_type']}: {pattern['success_rate']:.0%} success rate ({pattern['occurrences']} uses)\n"
        else:
            failure_insights_text = "\n\n        No historical failure data available yet (early in campaign)."

        from core.prompt_sanitizer import sanitize_for_prompt
        _goal = sanitize_for_prompt(str(context.get('goal', 'Not specified')), 'context.goal', 500)
        _recent = sanitize_for_prompt(json.dumps(context.get('recent_results', {}), indent=2), 'context.recent_results', 4000)

        return f"""Campaign Analysis and Decision Required:

        Campaign Goal: {_goal}
        Campaign ID: {context.get('campaign_id')}

        Current State:
        - Molecules Generated: {context.get('molecules_generated', 0)}
        - Successful Leads: {context.get('successful_leads', 0)}
        - Failed Attempts: {context.get('failure_count', 0)}
        - Timeline Remaining: {context.get('timeline_remaining', 'Unknown')} days
        - Budget Remaining: ${context.get('budget_remaining', 0):,.0f}
        - Success Rate: {context.get('success_rate', 0):.2%}

        Recent Literature Insights ({literature_insights.get('total_results', 0)} relevant papers):
        {insights_text}
        {failure_insights_text}

        Recent Results Summary:
        {_recent}

        Available Actions:
        1. generate_new_molecules - Create new molecular candidates
        2. optimize_existing_leads - Refine promising molecules
        3. pivot_strategy - Change approach based on failures
        4. screen_compounds - Virtual or experimental screening
        5. request_human_input - Escalate for human expertise
        6. complete_milestone - Mark phase complete and proceed
        7. adjust_parameters - Tune generation/optimization parameters
        8. expand_chemical_space - Explore new chemical families

        IMPORTANT: Consider the historical failures and cross-campaign patterns when making your decision.
        Avoid strategies that have consistently failed. Prefer strategies that have proven successful.
        If a specific phase or gate is problematic, adjust parameters or pivot strategy accordingly.

        Provide your decision in JSON format:
        {{
            "action": "selected_action",
            "confidence": 0.0-1.0,
            "reasoning": "explanation (reference failure patterns if relevant)",
            "parameters": {{specific parameters for the action}},
            "expected_outcome": "what this should achieve",
            "risk_assessment": "potential risks"
        }}"""

    def _parse_decision(self, response_text: str, context: dict) -> Dict[str, Any]:
        """Parse GPT-5 response into structured decision"""
        try:
            # Extract JSON from response
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)

            if json_match:
                decision_data = json.loads(json_match.group())
            else:
                # Fallback parsing if no JSON found
                decision_data = {
                    "action": DecisionType.GENERATE_NEW_MOLECULES.value,
                    "confidence": 0.5,
                    "reasoning": response_text[:200]
                }

            # Validate and enhance decision
            validated_decision = {
                "action": decision_data.get("action", DecisionType.GENERATE_NEW_MOLECULES.value),
                "confidence": float(decision_data.get("confidence", 0.7)),
                "reasoning": decision_data.get("reasoning", "Autonomous decision based on campaign state"),
                "parameters": self._get_action_parameters(decision_data, context),
                "expected_outcome": decision_data.get("expected_outcome", "Progress toward campaign goal"),
                "risk_assessment": decision_data.get("risk_assessment", "Standard risk profile"),
                "timestamp": datetime.utcnow().isoformat(),
                "campaign_id": context.get("campaign_id")
            }

            return validated_decision

        except Exception as e:
            logger.error(f"Failed to parse decision: {str(e)}")
            return self._fallback_decision(context)

    def _get_action_parameters(self, decision_data: dict, context: dict) -> Dict[str, Any]:
        """Generate appropriate parameters for the chosen action"""
        action = decision_data.get("action")
        params = decision_data.get("parameters", {})

        # Add default parameters based on action type
        if action == DecisionType.GENERATE_NEW_MOLECULES.value:
            return {
                "count": params.get("count", min(100, context.get("budget_remaining", 1000) // 10)),
                "strategy": params.get("strategy", "diverse_exploration"),
                "constraints": params.get("constraints", context.get("constraints", [])),
                **params
            }
        elif action == DecisionType.OPTIMIZE_EXISTING_LEADS.value:
            return {
                "molecule_ids": params.get("molecule_ids", []),
                "optimization_goals": params.get("optimization_goals", ["potency", "selectivity", "admet"]),
                "iterations": params.get("iterations", 5),
                **params
            }
        elif action == DecisionType.PIVOT_STRATEGY.value:
            return {
                "new_approach": params.get("new_approach", "alternative_scaffold"),
                "reason": params.get("reason", "Low success rate with current approach"),
                **params
            }

        return params

    def _apply_decision_weights(self, decision: Dict[str, Any]) -> Dict[str, Any]:
        """Apply learned weights to influence decision confidence"""
        action = decision.get("action")

        if action in self.decision_weights:
            weight = self.decision_weights[action]
            decision["confidence"] = min(1.0, decision["confidence"] * weight)
            decision["weight_applied"] = weight

        return decision

    async def learn_from_outcome(self, decision: dict, outcome: dict, campaign_id: str = None, iteration_number: int = 1) -> None:
        """
        Update decision weights based on outcome success.

        Args:
            decision: The decision that was made
            outcome: Results from executing the decision
            campaign_id: Campaign identifier (optional, for learning pattern persistence)
            iteration_number: Current iteration number (optional)
        """
        try:
            action = decision.get("action")
            success = outcome.get("success", False)
            impact_score = outcome.get("impact_score", 0.5)

            # Update weight based on outcome
            if action in self.decision_weights:
                current_weight = self.decision_weights[action]

                # Learning rate
                learning_rate = 0.1

                # Update weight: increase for success, decrease for failure
                if success:
                    adjustment = learning_rate * impact_score
                else:
                    adjustment = -learning_rate * (1 - impact_score)

                # Apply adjustment with bounds
                new_weight = max(0.1, min(2.0, current_weight + adjustment))
                self.decision_weights[action] = new_weight

                logger.info(f"Updated weight for {action}: {current_weight:.2f} -> {new_weight:.2f}")

            # Store learning pattern to Research DB
            if campaign_id:
                await self.store_learning_pattern(decision, outcome, campaign_id, iteration_number)

        except Exception as e:
            logger.error(f"Failed to learn from outcome: {str(e)}")

    async def store_learning_pattern(self, decision: dict, outcome: dict, campaign_id: str, iteration_number: int = 1) -> None:
        """
        Store decision-outcome pair for future learning to Research DB.

        Args:
            decision: The decision that was made
            outcome: Result of executing the decision
            campaign_id: Campaign identifier
            iteration_number: Current iteration number
        """
        try:
            import sys
            import os
            import uuid
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from core.db_helper import execute_sql

            pattern_id = str(uuid.uuid4())
            pattern_hash = self._generate_pattern_hash(decision, outcome)
            pattern_type = decision.get("action", "unknown")
            success_rate = 1.0 if outcome.get("success") else 0.0

            # Import enum converter to handle PhaseAction serialization
            import sys
            import os
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from workflow_engine import convert_enums_to_values

            # Convert any PhaseAction enums to values before JSON serialization
            serializable_context = convert_enums_to_values({
                "decision": decision,
                "outcome": outcome
            })
            context_json = json.dumps(serializable_context, default=str)

            # Insert or update learning pattern (upsert logic)
            # First, try to find existing pattern
            from core.db_helper import query_sql
            existing = await query_sql("""
                SELECT pattern_id, occurrence_count, success_rate
                FROM learning_patterns
                WHERE pattern_hash = %s AND campaign_id = %s
            """, (pattern_hash, campaign_id))

            if existing:
                # Update existing pattern with running average
                old_count = existing[0]['occurrence_count']
                old_success_rate = existing[0]['success_rate']
                new_count = old_count + 1
                new_success_rate = (old_success_rate * old_count + success_rate) / new_count

                await execute_sql("""
                    UPDATE learning_patterns
                    SET occurrence_count = %s,
                        success_rate = %s,
                        last_seen = GETUTCDATE(),
                        context = %s
                    WHERE pattern_id = %s
                """, (new_count, new_success_rate, context_json, existing[0]['pattern_id']))

                logger.info(f"Updated learning pattern {pattern_hash[:8]} for campaign {campaign_id}: {old_success_rate:.2%} -> {new_success_rate:.2%}")
            else:
                # Insert new pattern
                await execute_sql("""
                    INSERT INTO learning_patterns (
                        pattern_id, campaign_id, iteration_number, discovered_at,
                        pattern_hash, pattern_type, success_rate, occurrence_count,
                        context
                    ) VALUES (%s, %s, %s, GETUTCDATE(), %s, %s, %s, 1, %s)
                """, (
                    pattern_id,
                    campaign_id,
                    iteration_number,
                    pattern_hash,
                    pattern_type,
                    success_rate,
                    context_json
                ))

                logger.info(f"Stored new learning pattern {pattern_hash[:8]} for campaign {campaign_id}")

            # ALSO STORE TO PINECONE for similarity search and cross-campaign learning
            try:
                from core.pinecone_client import get_pinecone_client
                pinecone_client = get_pinecone_client()

                await pinecone_client.store_learning_pattern(
                    campaign_id=campaign_id,
                    decision=decision,
                    outcome=outcome,
                    context={"iteration": iteration_number}
                )
                logger.info(f"Stored learning pattern to Pinecone for campaign {campaign_id}")
            except Exception as pinecone_error:
                # Don't fail SQL storage if Pinecone fails, but log the error
                logger.error(f"Failed to store learning pattern to Pinecone: {str(pinecone_error)}", exc_info=True)

        except Exception as e:
            logger.error(f"Failed to store learning pattern: {str(e)}", exc_info=True)

    def _generate_pattern_hash(self, decision: dict, outcome: dict) -> str:
        """Generate unique hash for decision-outcome pattern"""
        import hashlib

        pattern_str = f"{decision.get('action')}_{decision.get('parameters')}_{outcome.get('success')}"
        return hashlib.sha256(pattern_str.encode()).hexdigest()[:16]

    async def _store_decision(self, campaign_id: str, decision: dict) -> None:
        """Store decision in database for audit trail"""
        try:
            if self.db_manager:
                await self.db_manager.store_campaign_decision({
                    "campaign_id": campaign_id,
                    "timestamp": datetime.utcnow(),
                    "decision_type": decision.get("action"),
                    "reasoning": decision.get("reasoning"),
                    "input_context": json.dumps(decision.get("parameters", {})),
                    "confidence": decision.get("confidence", 0.0)
                })
        except Exception as e:
            logger.error(f"Failed to store decision: {str(e)}")

    def _fallback_decision(self, context: dict) -> Dict[str, Any]:
        """Fallback decision when GPT-5 is unavailable"""
        # Simple rule-based fallback
        if context.get("successful_leads", 0) > 5:
            action = DecisionType.OPTIMIZE_EXISTING_LEADS.value
        elif context.get("failure_count", 0) > 10:
            action = DecisionType.PIVOT_STRATEGY.value
        else:
            action = DecisionType.GENERATE_NEW_MOLECULES.value

        return {
            "action": action,
            "confidence": 0.3,
            "reasoning": "Fallback decision based on simple rules",
            "parameters": self._get_action_parameters({"action": action}, context),
            "expected_outcome": "Continue campaign progress",
            "risk_assessment": "Using fallback logic - reduced confidence",
            "timestamp": datetime.utcnow().isoformat(),
            "campaign_id": context.get("campaign_id"),
            "fallback": True
        }

    async def get_decision_history(self, campaign_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Retrieve recent decisions for a campaign"""
        if self.db_manager:
            return await self.db_manager.get_campaign_decisions(campaign_id, limit)
        return []

    def get_current_weights(self) -> Dict[str, float]:
        """Get current decision weights for monitoring"""
        return self.decision_weights.copy()
