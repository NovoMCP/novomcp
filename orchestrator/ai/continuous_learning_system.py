"""
Continuous Learning System for Drug Discovery Campaigns
Extracts patterns from results and updates strategy weights
"""

import json
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime
import hashlib
import numpy as np
from collections import defaultdict

logger = logging.getLogger(__name__)

class ContinuousLearningSystem:
    """
    Learn from every campaign iteration to improve future decision-making.
    Accumulates knowledge across campaigns while maintaining tenant isolation.
    """

    def __init__(self, azure_client, db_manager=None):
        self.azure_client = azure_client
        self.db_manager = db_manager
        self.pattern_cache = defaultdict(lambda: {"count": 0, "success_rate": 0.0})
        self.negative_patterns = set()  # Track what doesn't work

    async def extract_patterns(self, results: dict) -> Dict[str, Any]:
        """
        Extract learnable patterns from campaign results.

        Args:
            results: Campaign execution results including:
                - campaign_id: Campaign identifier
                - action_taken: What action was executed
                - molecules: Generated/optimized molecules
                - scores: ADMET and activity scores
                - success: Boolean outcome
                - failure_reasons: Why something failed

        Returns:
            Extracted patterns for learning
        """
        try:
            patterns = {
                "successful_strategies": [],
                "failure_modes": [],
                "optimization_insights": [],
                "molecular_patterns": [],
                "timestamp": datetime.utcnow().isoformat()
            }

            # Analyze results with GPT-5 for deeper insights
            analysis_prompt = self._build_analysis_prompt(results)

            ai_analysis = await self.azure_client.complete(
                prompt=analysis_prompt,
                system_prompt="""You are an expert at analyzing drug discovery results.
                Extract actionable patterns that can improve future campaigns.
                Focus on what worked, what failed, and why.""",
                temperature=0.2,
                max_tokens=1500
            )

            if ai_analysis.get("success"):
                patterns.update(self._parse_ai_analysis(ai_analysis.get("response")))

            # Extract molecular structure patterns
            if results.get("molecules"):
                molecular_patterns = await self._analyze_molecular_patterns(results["molecules"])
                patterns["molecular_patterns"] = molecular_patterns

            # Identify failure patterns
            if not results.get("success"):
                failure_pattern = self._extract_failure_pattern(results)
                patterns["failure_modes"].append(failure_pattern)

                # Report to negative data service
                await self.report_to_negative_data(failure_pattern)

            # Identify success patterns
            else:
                success_pattern = self._extract_success_pattern(results)
                patterns["successful_strategies"].append(success_pattern)

            # Store patterns for future use
            await self._store_patterns(patterns, results.get("campaign_id"))

            logger.info(f"Extracted {len(patterns['successful_strategies'])} success and {len(patterns['failure_modes'])} failure patterns")

            return patterns

        except Exception as e:
            logger.error(f"Pattern extraction failed: {str(e)}")
            return {"error": str(e), "patterns": []}

    def _build_analysis_prompt(self, results: dict) -> str:
        """Build prompt for GPT-5 pattern analysis"""
        return f"""Analyze these drug discovery results for learnable patterns:

        Action Taken: {results.get('action_taken', 'Unknown')}
        Success: {results.get('success', False)}

        Molecules Generated: {len(results.get('molecules', []))}
        Best Score: {results.get('best_score', 0)}
        Average Score: {results.get('avg_score', 0)}

        Failure Reasons: {json.dumps(results.get('failure_reasons', []))}

        Constraints Applied: {json.dumps(results.get('constraints', []))}

        Extract:
        1. What strategies worked well?
        2. What caused failures?
        3. What molecular features correlated with success?
        4. What optimization insights emerged?
        5. What should be tried differently next time?

        Format as JSON with keys: strategies, failures, molecular_insights, optimization_tips, recommendations"""

    def _parse_ai_analysis(self, response_text: str) -> Dict[str, List]:
        """Parse GPT-5 analysis into structured patterns"""
        try:
            import re
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)

            if json_match:
                analysis = json.loads(json_match.group())
                return {
                    "successful_strategies": analysis.get("strategies", []),
                    "failure_modes": analysis.get("failures", []),
                    "optimization_insights": analysis.get("optimization_tips", [])
                }
        except Exception as e:
            logger.error(f"Failed to parse AI analysis: {str(e)}")

        return {}

    async def _analyze_molecular_patterns(self, molecules: List[Dict]) -> List[Dict]:
        """Analyze molecular structures for patterns"""
        patterns = []

        try:
            # Group molecules by success
            successful = [m for m in molecules if m.get("score", 0) > 0.7]
            failed = [m for m in molecules if m.get("score", 0) < 0.3]

            if successful:
                # Extract common features from successful molecules
                pattern = {
                    "type": "molecular_success",
                    "count": len(successful),
                    "avg_score": np.mean([m.get("score", 0) for m in successful]),
                    "common_features": self._extract_common_features(successful)
                }
                patterns.append(pattern)

            if failed:
                # Extract problematic features
                pattern = {
                    "type": "molecular_failure",
                    "count": len(failed),
                    "avg_score": np.mean([m.get("score", 0) for m in failed]),
                    "problematic_features": self._extract_common_features(failed)
                }
                patterns.append(pattern)

        except Exception as e:
            logger.error(f"Molecular pattern analysis failed: {str(e)}")

        return patterns

    def _extract_common_features(self, molecules: List[Dict]) -> Dict:
        """Extract common molecular features"""
        features = {
            "avg_mw": np.mean([m.get("properties", {}).get("molecular_weight", 0) for m in molecules]),
            "avg_logp": np.mean([m.get("properties", {}).get("logp", 0) for m in molecules]),
            "common_scaffolds": [],  # Would require RDKit analysis
            "functional_groups": []   # Would require chemical analysis
        }
        return features

    def _extract_failure_pattern(self, results: dict) -> Dict[str, Any]:
        """Extract pattern from failed attempt"""
        pattern = {
            "pattern_id": self._generate_pattern_id(results),
            "action": results.get("action_taken"),
            "failure_type": results.get("failure_reasons", ["unknown"])[0] if results.get("failure_reasons") else "unknown",
            "context": {
                "constraints": results.get("constraints", []),
                "parameters": results.get("parameters", {}),
                "scores": results.get("scores", {})
            },
            "timestamp": datetime.utcnow().isoformat()
        }

        # Add to negative patterns
        self.negative_patterns.add(pattern["pattern_id"])

        return pattern

    def _extract_success_pattern(self, results: dict) -> Dict[str, Any]:
        """Extract pattern from successful attempt"""
        return {
            "pattern_id": self._generate_pattern_id(results),
            "action": results.get("action_taken"),
            "success_factors": results.get("success_factors", []),
            "best_score": results.get("best_score", 0),
            "parameters_used": results.get("parameters", {}),
            "timestamp": datetime.utcnow().isoformat()
        }

    def _generate_pattern_id(self, results: dict) -> str:
        """Generate unique ID for a pattern"""
        pattern_str = f"{results.get('action_taken')}_{results.get('success')}_{results.get('campaign_id')}"
        return hashlib.sha256(pattern_str.encode()).hexdigest()[:16]

    async def update_strategy_weights(self, patterns: dict) -> None:
        """
        Update decision-making weights based on extracted patterns.

        Args:
            patterns: Extracted patterns from campaign results
        """
        try:
            # Count pattern occurrences
            for strategy in patterns.get("successful_strategies", []):
                pattern_id = strategy.get("pattern_id")
                if pattern_id:
                    self.pattern_cache[pattern_id]["count"] += 1
                    self.pattern_cache[pattern_id]["success_rate"] = (
                        self.pattern_cache[pattern_id]["success_rate"] * 0.9 + 1.0 * 0.1
                    )  # Exponential moving average

            for failure in patterns.get("failure_modes", []):
                pattern_id = failure.get("pattern_id")
                if pattern_id:
                    self.pattern_cache[pattern_id]["count"] += 1
                    self.pattern_cache[pattern_id]["success_rate"] = (
                        self.pattern_cache[pattern_id]["success_rate"] * 0.9 + 0.0 * 0.1
                    )

            # Update database with new weights
            if self.db_manager:
                await self._persist_pattern_weights()

            logger.info(f"Updated strategy weights for {len(self.pattern_cache)} patterns")

        except Exception as e:
            logger.error(f"Failed to update strategy weights: {str(e)}")

    async def _persist_pattern_weights(self) -> None:
        """Save pattern weights to database"""
        try:
            for pattern_id, data in self.pattern_cache.items():
                await self.db_manager.update_pattern({
                    "pattern_hash": pattern_id,
                    "occurrence_count": data["count"],
                    "success_rate": data["success_rate"],
                    "last_seen": datetime.utcnow()
                })
        except Exception as e:
            logger.error(f"Failed to persist pattern weights: {str(e)}")

    async def report_to_negative_data(self, failure_pattern: dict) -> None:
        """
        Report failure pattern to negative data service.

        Args:
            failure_pattern: Pattern describing what didn't work
        """
        try:
            # Format for negative data service
            negative_entry = {
                "molecule_data": failure_pattern.get("context", {}).get("molecules", []),
                "failure_reason": failure_pattern.get("failure_type"),
                "scores": failure_pattern.get("context", {}).get("scores", {}),
                "timestamp": failure_pattern.get("timestamp"),
                "campaign_context": {
                    "action": failure_pattern.get("action"),
                    "parameters": failure_pattern.get("context", {}).get("parameters", {})
                }
            }

            # In production, call negative data service
            # For now, log the entry
            logger.info(f"Reported failure pattern to negative data: {failure_pattern.get('pattern_id')}")

            # Store locally for quick lookup
            if self.db_manager:
                await self.db_manager.store_negative_pattern(negative_entry)

        except Exception as e:
            logger.error(f"Failed to report to negative data: {str(e)}")

    async def get_recommendations(self, campaign_context: dict) -> List[Dict[str, Any]]:
        """
        Get recommendations based on learned patterns.

        Args:
            campaign_context: Current campaign state

        Returns:
            List of recommendations with confidence scores
        """
        recommendations = []

        try:
            # Check for similar successful patterns
            for pattern_id, data in self.pattern_cache.items():
                if data["success_rate"] > 0.7 and data["count"] > 5:
                    recommendations.append({
                        "pattern_id": pattern_id,
                        "confidence": data["success_rate"],
                        "occurrences": data["count"],
                        "recommendation": "Consider using this successful pattern"
                    })

            # Check for patterns to avoid
            for pattern_id in self.negative_patterns:
                if pattern_id in self.pattern_cache and self.pattern_cache[pattern_id]["count"] > 3:
                    recommendations.append({
                        "pattern_id": pattern_id,
                        "confidence": 0.9,
                        "warning": "Avoid this pattern - high failure rate",
                        "failure_count": self.pattern_cache[pattern_id]["count"]
                    })

            # Sort by confidence
            recommendations.sort(key=lambda x: x.get("confidence", 0), reverse=True)

        except Exception as e:
            logger.error(f"Failed to generate recommendations: {str(e)}")

        return recommendations[:10]  # Return top 10 recommendations

    async def _store_patterns(self, patterns: dict, campaign_id: str) -> None:
        """Store extracted patterns in database"""
        try:
            if self.db_manager:
                for pattern_type, pattern_list in patterns.items():
                    if isinstance(pattern_list, list):
                        for pattern in pattern_list:
                            await self.db_manager.store_learning_pattern({
                                "campaign_id": campaign_id,
                                "pattern_type": pattern_type,
                                "pattern_data": json.dumps(pattern),
                                "timestamp": datetime.utcnow()
                            })
        except Exception as e:
            logger.error(f"Failed to store patterns: {str(e)}")

    async def share_learning_across_campaigns(self, tenant_id: str) -> Dict[str, Any]:
        """
        Share learning patterns across campaigns within the same tenant.

        Args:
            tenant_id: Organization identifier for data isolation

        Returns:
            Aggregated learning insights
        """
        try:
            if self.db_manager:
                # Get all patterns for this tenant
                tenant_patterns = await self.db_manager.get_tenant_patterns(tenant_id)

                # Aggregate insights
                insights = {
                    "total_patterns": len(tenant_patterns),
                    "success_patterns": sum(1 for p in tenant_patterns if p.get("success_rate", 0) > 0.7),
                    "failure_patterns": sum(1 for p in tenant_patterns if p.get("success_rate", 0) < 0.3),
                    "most_successful_actions": self._get_top_actions(tenant_patterns, successful=True),
                    "most_failed_actions": self._get_top_actions(tenant_patterns, successful=False),
                    "learning_velocity": self._calculate_learning_velocity(tenant_patterns),
                    "patterns": tenant_patterns  # Include the actual patterns
                }

                return insights

        except Exception as e:
            logger.error(f"Failed to share learning: {str(e)}")
            return {}

    def _get_top_actions(self, patterns: List[Dict], successful: bool = True) -> List[Dict]:
        """Get top performing or failing actions"""
        action_scores = defaultdict(list)

        for pattern in patterns:
            action = pattern.get("pattern_type")
            score = pattern.get("success_rate", 0)

            if (successful and score > 0.7) or (not successful and score < 0.3):
                action_scores[action].append(score)

        # Calculate average scores
        top_actions = []
        for action, scores in action_scores.items():
            top_actions.append({
                "action": action,
                "avg_score": np.mean(scores),
                "count": len(scores)
            })

        # Sort and return top 5
        top_actions.sort(key=lambda x: x["avg_score"], reverse=successful)
        return top_actions[:5]

    def _calculate_learning_velocity(self, patterns: List[Dict]) -> float:
        """Calculate how fast the system is learning"""
        if not patterns:
            return 0.0

        # Get patterns from last 30 days
        cutoff = datetime.utcnow().timestamp() - (30 * 24 * 3600)
        recent_patterns = [
            p for p in patterns
            if datetime.fromisoformat(p.get("timestamp", "2020-01-01")).timestamp() > cutoff
        ]

        # Calculate improvement rate
        if len(recent_patterns) < 2:
            return 0.0

        early_success = np.mean([p.get("success_rate", 0) for p in recent_patterns[:len(recent_patterns)//2]])
        late_success = np.mean([p.get("success_rate", 0) for p in recent_patterns[len(recent_patterns)//2:]])

        return max(0, late_success - early_success)  # Positive means improving