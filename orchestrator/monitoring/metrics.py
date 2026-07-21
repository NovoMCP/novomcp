"""
Metrics Collection for Agentic AI System
Tracks performance, decisions, and campaign progress
"""

import time
import asyncio
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from collections import defaultdict, deque
import logging
import json

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Collect and aggregate metrics for monitoring"""

    def __init__(self, window_size: int = 1000):
        """
        Initialize metrics collector

        Args:
            window_size: Number of recent metrics to keep in memory
        """
        self.window_size = window_size
        self._metrics = defaultdict(lambda: deque(maxlen=window_size))
        self._counters = defaultdict(int)
        self._gauges = {}
        self._timings = defaultdict(list)
        self._start_time = datetime.utcnow()

    def record_service_call(
        self,
        service: str,
        endpoint: str,
        duration: float,
        status: str,
        status_code: Optional[int] = None
    ):
        """Record service call metrics"""
        metric = {
            'timestamp': datetime.utcnow().isoformat(),
            'service': service,
            'endpoint': endpoint,
            'duration_ms': duration * 1000,
            'status': status,
            'status_code': status_code
        }

        self._metrics['service_calls'].append(metric)
        self._counters[f'service_calls.{service}.{status}'] += 1

        # Track timing statistics
        self._timings[f'{service}.{endpoint}'].append(duration * 1000)

    def record_campaign_decision(
        self,
        campaign_id: str,
        action: str,
        confidence: float,
        duration: float,
        success: bool
    ):
        """Record campaign decision metrics"""
        metric = {
            'timestamp': datetime.utcnow().isoformat(),
            'campaign_id': campaign_id,
            'action': action,
            'confidence': confidence,
            'duration_ms': duration * 1000,
            'success': success
        }

        self._metrics['campaign_decisions'].append(metric)
        self._counters[f'decisions.{action}.{"success" if success else "failure"}'] += 1

    def record_learning_insight(
        self,
        insight_type: str,
        source: str,
        relevance_score: float,
        applied: bool
    ):
        """Record learning system insights"""
        metric = {
            'timestamp': datetime.utcnow().isoformat(),
            'type': insight_type,
            'source': source,
            'relevance_score': relevance_score,
            'applied': applied
        }

        self._metrics['learning_insights'].append(metric)
        self._counters[f'insights.{insight_type}.{"applied" if applied else "ignored"}'] += 1

    def record_literature_scan(
        self,
        source: str,
        papers_found: int,
        relevant_papers: int,
        duration: float
    ):
        """Record literature scanning metrics"""
        metric = {
            'timestamp': datetime.utcnow().isoformat(),
            'source': source,
            'papers_found': papers_found,
            'relevant_papers': relevant_papers,
            'duration_ms': duration * 1000,
            'relevance_rate': relevant_papers / papers_found if papers_found > 0 else 0
        }

        self._metrics['literature_scans'].append(metric)
        self._counters[f'literature.{source}.total'] += papers_found
        self._counters[f'literature.{source}.relevant'] += relevant_papers

    def record_websocket_event(
        self,
        campaign_id: str,
        event_type: str,
        message_size: int
    ):
        """Record WebSocket communication metrics"""
        metric = {
            'timestamp': datetime.utcnow().isoformat(),
            'campaign_id': campaign_id,
            'event_type': event_type,
            'message_size': message_size
        }

        self._metrics['websocket_events'].append(metric)
        self._counters[f'websocket.{event_type}'] += 1

    def set_gauge(self, name: str, value: float):
        """Set a gauge metric (current value)"""
        self._gauges[name] = {
            'value': value,
            'timestamp': datetime.utcnow().isoformat()
        }

    def increment_counter(self, name: str, value: int = 1):
        """Increment a counter metric"""
        self._counters[name] += value

    def get_service_stats(self, service: Optional[str] = None) -> Dict[str, Any]:
        """Get service call statistics"""
        calls = list(self._metrics['service_calls'])

        if service:
            calls = [c for c in calls if c['service'] == service]

        if not calls:
            return {'message': 'No service calls recorded'}

        # Calculate statistics
        total_calls = len(calls)
        success_calls = sum(1 for c in calls if c['status'] == 'success')
        failed_calls = sum(1 for c in calls if c['status'] == 'error')

        durations = [c['duration_ms'] for c in calls]
        avg_duration = sum(durations) / len(durations) if durations else 0

        # Group by service
        by_service = defaultdict(lambda: {'success': 0, 'error': 0, 'total': 0})
        for call in calls:
            by_service[call['service']]['total'] += 1
            by_service[call['service']][call['status']] += 1

        return {
            'total_calls': total_calls,
            'success_calls': success_calls,
            'failed_calls': failed_calls,
            'success_rate': success_calls / total_calls if total_calls > 0 else 0,
            'avg_duration_ms': avg_duration,
            'by_service': dict(by_service),
            'recent_failures': [
                c for c in calls[-10:] if c['status'] == 'error'
            ]
        }

    def get_campaign_stats(self, campaign_id: Optional[str] = None) -> Dict[str, Any]:
        """Get campaign decision statistics"""
        decisions = list(self._metrics['campaign_decisions'])

        if campaign_id:
            decisions = [d for d in decisions if d['campaign_id'] == campaign_id]

        if not decisions:
            return {'message': 'No campaign decisions recorded'}

        # Calculate statistics
        total_decisions = len(decisions)
        successful = sum(1 for d in decisions if d['success'])
        avg_confidence = sum(d['confidence'] for d in decisions) / total_decisions

        # Group by action
        by_action = defaultdict(lambda: {'success': 0, 'failure': 0, 'total': 0})
        for decision in decisions:
            by_action[decision['action']]['total'] += 1
            by_action[decision['action']]['success' if decision['success'] else 'failure'] += 1

        return {
            'total_decisions': total_decisions,
            'successful_decisions': successful,
            'success_rate': successful / total_decisions if total_decisions > 0 else 0,
            'avg_confidence': avg_confidence,
            'by_action': dict(by_action),
            'recent_decisions': decisions[-10:]
        }

    def get_timing_stats(self, service: Optional[str] = None) -> Dict[str, Any]:
        """Get timing statistics for service calls"""
        stats = {}

        for key, timings in self._timings.items():
            if service and not key.startswith(service):
                continue

            if timings:
                sorted_timings = sorted(timings)
                stats[key] = {
                    'count': len(timings),
                    'min_ms': min(timings),
                    'max_ms': max(timings),
                    'avg_ms': sum(timings) / len(timings),
                    'p50_ms': sorted_timings[len(sorted_timings) // 2],
                    'p95_ms': sorted_timings[int(len(sorted_timings) * 0.95)],
                    'p99_ms': sorted_timings[int(len(sorted_timings) * 0.99)]
                }

        return stats

    def get_summary(self) -> Dict[str, Any]:
        """Get overall metrics summary"""
        uptime = datetime.utcnow() - self._start_time

        return {
            'uptime_seconds': uptime.total_seconds(),
            'counters': dict(self._counters),
            'gauges': dict(self._gauges),
            'service_stats': self.get_service_stats(),
            'campaign_stats': self.get_campaign_stats(),
            'timing_stats': self.get_timing_stats(),
            'recent_insights': list(self._metrics['learning_insights'])[-10:],
            'recent_literature': list(self._metrics['literature_scans'])[-5:]
        }

    def export_metrics(self) -> str:
        """Export metrics in Prometheus format"""
        lines = []

        # Export counters
        for name, value in self._counters.items():
            metric_name = name.replace('.', '_').replace('-', '_')
            lines.append(f'novomcp_{metric_name}_total {value}')

        # Export gauges
        for name, data in self._gauges.items():
            metric_name = name.replace('.', '_').replace('-', '_')
            lines.append(f'novomcp_{metric_name} {data["value"]}')

        # Export timing percentiles
        for key, stats in self.get_timing_stats().items():
            metric_base = f'novomcp_{key.replace(".", "_").replace("-", "_")}'
            lines.append(f'{metric_base}_p50_ms {stats["p50_ms"]}')
            lines.append(f'{metric_base}_p95_ms {stats["p95_ms"]}')
            lines.append(f'{metric_base}_p99_ms {stats["p99_ms"]}')

        return '\n'.join(lines)

    def clear_old_metrics(self, hours: int = 24):
        """Clear metrics older than specified hours"""
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        for metric_type, metrics in self._metrics.items():
            # Filter metrics newer than cutoff
            filtered = [
                m for m in metrics
                if datetime.fromisoformat(m['timestamp']) > cutoff
            ]

            # Replace with filtered metrics
            self._metrics[metric_type] = deque(filtered, maxlen=self.window_size)

        logger.info(f"Cleared metrics older than {hours} hours")


# Global metrics collector instance
_metrics_collector = MetricsCollector()


def get_metrics_collector() -> MetricsCollector:
    """Get global metrics collector instance"""
    return _metrics_collector


# Helper decorators for automatic metric collection
def track_service_call(service: str, endpoint: str):
    """Decorator to track service call metrics"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status = 'success'
            status_code = None

            try:
                result = await func(*args, **kwargs)
                if isinstance(result, dict):
                    status = result.get('status', 'success')
                    status_code = result.get('status_code')
                return result

            except Exception as e:
                status = 'error'
                raise

            finally:
                duration = time.time() - start_time
                _metrics_collector.record_service_call(
                    service, endpoint, duration, status, status_code
                )

        return wrapper
    return decorator


def track_decision(campaign_id: str, action: str):
    """Decorator to track campaign decision metrics"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            success = False
            confidence = 0.5

            try:
                result = await func(*args, **kwargs)
                if isinstance(result, dict):
                    success = result.get('success', False)
                    confidence = result.get('confidence', 0.5)
                return result

            except Exception as e:
                success = False
                raise

            finally:
                duration = time.time() - start_time
                _metrics_collector.record_campaign_decision(
                    campaign_id, action, confidence, duration, success
                )

        return wrapper
    return decorator