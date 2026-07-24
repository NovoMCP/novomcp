"""Single source of truth for the NovoMCP engine version.

Bump this on every release. Referenced by:
- boot-time update check (`core/updater.py`)
- `get_platform_info` tool response
- CI release checks
"""
__version__ = "1.1.2"
