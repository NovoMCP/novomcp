"""
Prompt injection sanitization for LLM inputs.
Truncates, strips control chars, and blocks injection pattern matches.
"""

import re
import logging

logger = logging.getLogger(__name__)

_INJECTION_PATTERNS = [
    re.compile(r'(?i)ignore\s+(all\s+)?previous\s+instructions'),
    re.compile(r'(?i)disregard\s+(all\s+)?above'),
    re.compile(r'(?i)new\s+instructions?\s*:'),
    re.compile(r'(?i)system\s*:\s*you\s+are'),
    re.compile(r'(?i)override\s+(system|instructions)'),
    re.compile(r'(?i)reveal\s+(your|the)\s+(system\s+)?prompt'),
    re.compile(r'(?i)you\s+are\s+now\s+a'),
    re.compile(r'(?i)forget\s+(all\s+)?(previous|prior|above)'),
    re.compile(r'(?i)act\s+as\s+(if|though)\s+you'),
    re.compile(r'(?i)<\s*/?\s*system\s*>'),
]

_INJECTION_REPLACEMENT = "[content removed: prompt injection detected]"

# Control characters to strip (keep newlines and tabs)
_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def sanitize_for_prompt(value: str, field_name: str, max_length: int = 2000) -> str:
    """
    Sanitize a user-provided value before interpolating into an LLM prompt.

    - Truncates to max_length
    - Strips control characters (preserves newlines/tabs)
    - Strips detected prompt injection patterns
    """
    if not isinstance(value, str):
        value = str(value) if value is not None else ""

    # Strip control characters
    value = _CONTROL_CHARS.sub('', value)

    # Truncate
    if len(value) > max_length:
        value = value[:max_length] + "...[truncated]"
        logger.warning(
            "Prompt input truncated",
            extra={"field": field_name, "original_length": len(value)}
        )

    # Strip injection patterns
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(value):
            logger.warning(
                "Prompt injection blocked",
                extra={"field": field_name, "pattern": pattern.pattern, "value_preview": value[:100]}
            )
            value = pattern.sub(_INJECTION_REPLACEMENT, value)

    return value


def sanitize_rows_for_prompt(rows: list, max_rows: int = 50) -> list:
    """
    Sanitize a list of data rows (dicts) pulled from external sources
    before returning them in tool responses that enter AI context.
    """
    sanitized = []
    for row in rows[:max_rows]:
        if isinstance(row, dict):
            sanitized.append({
                k: sanitize_for_prompt(str(v), f"row.{k}", max_length=500)
                if isinstance(v, str) else v
                for k, v in row.items()
            })
        else:
            sanitized.append(row)
    return sanitized
