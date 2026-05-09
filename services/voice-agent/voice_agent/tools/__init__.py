"""
Voice tool client — thin HTTP wrapper around Harsha's FastAPI endpoints.

All durable actions go through this module. No direct database writes.
Phase 0: stubs with correct signatures. Phase 1: replace stubs with real httpx calls.
"""

from voice_agent.tools.backend_client import BackendClient, BackendToolError

__all__ = ["BackendClient", "BackendToolError"]
