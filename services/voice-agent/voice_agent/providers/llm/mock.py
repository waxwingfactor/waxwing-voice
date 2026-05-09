"""
MockLLMAdapter — deterministic LLM adapter for tests.

No network calls. Yields scripted response strings (chunked as tokens) so tests
can assert on response content, token streaming, and cancellation behaviour
without mocking httpx internals or providing real API keys.

Design
------
The mock takes a list of scripted responses at construction time. Each call to
respond_streaming() consumes one script entry, yielding the response text as
individual word tokens (split on spaces) to simulate token streaming.

If the script list is exhausted and another call is made, MockLLMAdapter raises
AssertionError to fail loudly — the same pattern as MockSTTAdapter. This prevents
tests from silently passing with unexpected empty responses.

Usage in tests::

    llm = MockLLMAdapter(responses=[
        "Our one-bedroom units start at $1,800 per month.",
        "Yes, we allow pets with a $300 refundable deposit.",
        "I'll connect you with a leasing agent right away.",
    ])

    # Turn 1:
    tokens = [t async for t in await llm.respond_streaming(prompt, history)]
    assert "".join(tokens) == "Our one-bedroom units start at $1,800 per month."

    # Fail-loud test:
    llm2 = MockLLMAdapter(responses=["only one"])
    [t async for t in await llm2.respond_streaming(prompt, [])]
    [t async for t in await llm2.respond_streaming(prompt, [])]  # AssertionError!
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from voice_agent.providers.llm.protocol import LLMAdapter, LLMProviderError, Message

log = logging.getLogger("voice_agent.providers.llm.mock")


class MockLLMAdapter:
    """
    Deterministic LLM adapter for unit and integration tests.

    Implements the LLMAdapter protocol without network calls. Each call to
    respond_streaming() yields the next scripted response as word-level tokens.
    Fails loudly (AssertionError) if called more times than responses provided.

    Tracks call counts and last-seen prompts so tests can assert on them.
    """

    def __init__(
        self,
        responses: list[str] | None = None,
        fail_on_next: bool = False,
        fail_retryable: bool = False,
        delay_seconds: float = 0.0,
    ) -> None:
        """
        Args:
            responses:      Scripted response strings. Each call consumes one entry.
                            If None, defaults to empty list — any call raises AssertionError.
            fail_on_next:   If True, next respond_streaming raises LLMProviderError.
            fail_retryable: Whether the forced failure is retryable.
            delay_seconds:  Simulated per-token delay. 0 for fast tests.
        """
        self._responses: list[str] = responses or []
        self._response_index: int = 0
        self._fail_on_next = fail_on_next
        self._fail_retryable = fail_retryable
        self._delay_seconds = delay_seconds

        self._cancel_event: asyncio.Event = asyncio.Event()

        # Call tracking — assert on these in tests.
        self.respond_call_count: int = 0
        self.cancel_call_count: int = 0
        self.last_system_prompt: str | None = None
        self.last_history: list[Message] | None = None

    async def respond_streaming(
        self,
        system_prompt: str,
        conversation_history: list[Message],
    ) -> AsyncIterator[str]:
        """
        Yield scripted response tokens, ignoring the actual system prompt and history.

        Records the system_prompt and conversation_history so tests can assert that
        the correct context was passed.

        Raises:
            AssertionError:  If called more times than responses are provided.
            LLMProviderError: If fail_on_next is True.
        """
        self._cancel_event.clear()
        self.respond_call_count += 1
        # Record last call args (not logged — prompt may contain PII in integration tests)
        self.last_system_prompt = system_prompt
        self.last_history = list(conversation_history)

        # Handle forced failure
        if self._fail_on_next:
            self._fail_on_next = False  # consume the flag
            raise LLMProviderError(
                "MockLLMAdapter: simulated generation failure.",
                retryable=self._fail_retryable,
            )

        # Fail loudly if responses exhausted
        if self._response_index >= len(self._responses):
            raise AssertionError(
                f"MockLLMAdapter: respond_streaming called {self.respond_call_count} times "
                f"but only {len(self._responses)} response(s) were provided. "
                "Add more entries to the responses list."
            )

        response_text = self._responses[self._response_index]
        self._response_index += 1

        # Yield tokens word by word (simulates streaming)
        words = response_text.split(" ")
        for i, word in enumerate(words):
            if self._cancel_event.is_set():
                log.debug("MockLLMAdapter: stream cancelled mid-response")
                return
            if self._delay_seconds > 0:
                await asyncio.sleep(self._delay_seconds)
            # Add space before each word except the first (reconstruct the string)
            token = word if i == 0 else " " + word
            yield token

    async def cancel(self) -> None:
        """
        Signal cancellation. Idempotent.
        """
        self.cancel_call_count += 1
        self._cancel_event.set()
        log.debug("MockLLMAdapter.cancel called")


# Runtime check — verify MockLLMAdapter satisfies the LLMAdapter protocol.
# Runs at import time in dev/test environments and catches protocol drift.
assert isinstance(MockLLMAdapter(), LLMAdapter), (
    "MockLLMAdapter does not satisfy LLMAdapter protocol. "
    "Check protocol.py and mock.py for method signature drift."
)
