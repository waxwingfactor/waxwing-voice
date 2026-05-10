"""
Tests for the before_llm_cb and after_llm_cb factory functions in entrypoint.py.

These tests catch the bugs that the prior fix-it pass introduced or missed:
  - Bug D: coordinator prompt wiped before reaching Gemini (rebuild ran after append)
  - Bug F: property profile placeholder in system prompt (profile not loaded in prod path)
  - Medium 1: escalation path skipped system prompt rebuild
  - Medium 2: after_llm_cb emitted bogus tool_called event

The callbacks guard all chat_ctx manipulation with `if _LIVEKIT_AVAILABLE and chat_ctx is not None`.
To test them, we patch entrypoint._LIVEKIT_AVAILABLE = True and supply mock ChatMessage /
ChatContext objects that satisfy the isinstance checks used inside the callbacks.

We do NOT test the VoicePipelineAgent start sequence — that requires a live LiveKit room.
We DO test the callback closures directly, which is the layer that contains the bugs.

Test coverage:
  1. before_llm_cb: system prompt inserted as first message (rebuild works)
  2. before_llm_cb: caller segment added to state before handle_caller_turn
  3. before_llm_cb: coordinator prompt survives after system prompt rebuild (Bug D regression)
  4. before_llm_cb: escalation path also rebuilds system prompt + appends instruction (Medium 1)
  5. before_llm_cb: property profile placeholder absent when profile loaded (Bug F regression)
  6. after_llm_cb: agent segment added to state
  7. after_llm_cb: transcript flush scheduled
  8. after_llm_cb: does NOT emit tool_called event for plain LLM response (Medium 2 regression)
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal mock classes for ChatMessage and ChatContext
# (no livekit import needed — we only need duck-typed objects)
# ---------------------------------------------------------------------------


class _MockChatMessage:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content

    def __repr__(self) -> str:
        return f"ChatMessage(role={self.role!r}, content={self.content[:40]!r}...)"


class _MockChatContext:
    def __init__(self) -> None:
        self.messages: list[_MockChatMessage] = []


# ---------------------------------------------------------------------------
# Helpers to build test fixtures
# ---------------------------------------------------------------------------


def _make_mock_session(
    backend_call_id: str = "11111111-1111-1111-1111-111111111111",
    property_id: str = "22222222-2222-2222-2222-222222222222",
    property_name: str = "Maple Grove Apartments",
) -> Any:
    """
    Build a minimal VoiceSession with mocked backend client.
    Sets up _property_profile with a real property name (not placeholder).
    """
    from unittest.mock import MagicMock, AsyncMock
    from voice_agent.agent.session import VoiceSession
    from voice_agent.tools.backend_client import BackendClient
    from voice_agent.prompts.system_prompt import PropertyProfileSlot

    mock_client = MagicMock(spec=BackendClient)
    # handle_caller_turn is async — default turn result (no escalation, no coordinator)
    mock_client.save_transcript_segment = AsyncMock(return_value=MagicMock())
    mock_client.create_call_event = AsyncMock(return_value=MagicMock())

    session = VoiceSession(
        property_id=property_id,
        jwt_token="test-jwt",
        backend_client=mock_client,
    )
    session.state.backend_call_id = backend_call_id

    # Set a real property profile (Bug F regression setup)
    session._property_profile = PropertyProfileSlot(
        property_id=property_id,
        property_name=property_name,
        address="123 Maple Street, Austin TX 78701",
        description="A beautiful community in Austin.",
    )

    return session


def _make_chat_ctx_with_user_message(user_text: str) -> _MockChatContext:
    ctx = _MockChatContext()
    ctx.messages.append(_MockChatMessage(role="user", content=user_text))
    return ctx


def _make_chat_ctx_empty() -> _MockChatContext:
    return _MockChatContext()


# ---------------------------------------------------------------------------
# Patch targets
# ---------------------------------------------------------------------------

# All patches that make the callback think LiveKit is available and supply
# mock ChatMessage so the callback can construct messages.
_PATCHES = [
    # Make the module believe LiveKit is available
    patch("voice_agent.worker.entrypoint._LIVEKIT_AVAILABLE", True),
    # Replace the None-alias ChatMessage with our mock factory
    patch(
        "voice_agent.worker.entrypoint.ChatMessage",
        side_effect=lambda role, content: _MockChatMessage(role=role, content=content),
    ),
]


def _apply_patches(patches: list) -> tuple:
    """Start all patches and return (mocks, stack) for cleanup."""
    mocks = []
    for p in patches:
        mocks.append(p.start())
    return mocks


def _stop_patches(patches: list) -> None:
    for p in patches:
        p.stop()


# ---------------------------------------------------------------------------
# Test: before_llm_cb — system prompt is inserted (basic rebuild)
# ---------------------------------------------------------------------------


class TestBeforeLlmCbSystemPrompt:

    @pytest.mark.asyncio
    async def test_system_prompt_inserted_as_first_message(self) -> None:
        """
        before_llm_cb rebuilds the system prompt and inserts it at index 0 in
        chat_ctx.messages. This must happen regardless of whether there is caller text.
        """
        from voice_agent.worker.entrypoint import _make_before_llm_cb
        from voice_agent.tools.backend_client import BackendClient

        session = _make_mock_session()
        mock_client = MagicMock(spec=BackendClient)

        patches = _PATCHES[:]
        _apply_patches(patches)
        try:
            cb = _make_before_llm_cb(session, mock_client)
            chat_ctx = _make_chat_ctx_empty()
            await cb(agent=MagicMock(), chat_ctx=chat_ctx)

            assert len(chat_ctx.messages) >= 1
            first = chat_ctx.messages[0]
            assert first.role == "system"
            assert len(first.content) > 0
        finally:
            _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_system_prompt_replaces_stale_system_messages(self) -> None:
        """
        before_llm_cb strips all existing system messages and inserts a fresh one.
        Prevents duplication across turns.
        """
        from voice_agent.worker.entrypoint import _make_before_llm_cb
        from voice_agent.tools.backend_client import BackendClient

        session = _make_mock_session()
        mock_client = MagicMock(spec=BackendClient)

        patches = _PATCHES[:]
        _apply_patches(patches)
        try:
            cb = _make_before_llm_cb(session, mock_client)
            chat_ctx = _make_chat_ctx_empty()
            # Pre-populate with a stale system message from a previous turn
            chat_ctx.messages.append(_MockChatMessage(role="system", content="Old prompt"))
            chat_ctx.messages.append(_MockChatMessage(role="user", content="Hello"))

            await cb(agent=MagicMock(), chat_ctx=chat_ctx)

            system_messages = [m for m in chat_ctx.messages if m.role == "system"]
            # After rebuild: exactly one system message (the fresh one)
            assert len(system_messages) == 1
            assert "Old prompt" not in system_messages[0].content
        finally:
            _stop_patches(patches)


# ---------------------------------------------------------------------------
# Test: before_llm_cb — caller segment added to state
# ---------------------------------------------------------------------------


class TestBeforeLlmCbCallerSegment:

    @pytest.mark.asyncio
    async def test_caller_segment_added_before_handle_caller_turn(self) -> None:
        """
        before_llm_cb records the caller utterance in call state before
        calling handle_caller_turn. This ensures the segment is in state when
        retrieval/escalation runs.
        """
        from voice_agent.worker.entrypoint import _make_before_llm_cb
        from voice_agent.tools.backend_client import BackendClient

        session = _make_mock_session()
        mock_client = MagicMock(spec=BackendClient)

        # Mock handle_caller_turn to return a non-escalated result
        session.handle_caller_turn = AsyncMock(return_value={
            "escalated": False,
            "escalation_reason": None,
            "phase": "greeting",
            "booking_agent_prompt": None,
            "email_agent_prompt": None,
        })

        patches = _PATCHES[:]
        _apply_patches(patches)
        try:
            cb = _make_before_llm_cb(session, mock_client)
            chat_ctx = _make_chat_ctx_with_user_message("I want to see a unit")

            await cb(agent=MagicMock(), chat_ctx=chat_ctx)

            # Caller segment should be in state
            from voice_agent.state.call_state import SpeakerRole
            caller_segs = [
                s for s in session.state.transcript
                if s.speaker == SpeakerRole.CALLER
            ]
            assert len(caller_segs) == 1
            assert caller_segs[0].text == "I want to see a unit"
        finally:
            _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_handle_caller_turn_called_with_user_text(self) -> None:
        """before_llm_cb calls handle_caller_turn with the extracted user text."""
        from voice_agent.worker.entrypoint import _make_before_llm_cb
        from voice_agent.tools.backend_client import BackendClient

        session = _make_mock_session()
        session.handle_caller_turn = AsyncMock(return_value={
            "escalated": False,
            "escalation_reason": None,
            "phase": "greeting",
            "booking_agent_prompt": None,
            "email_agent_prompt": None,
        })
        mock_client = MagicMock(spec=BackendClient)

        patches = _PATCHES[:]
        _apply_patches(patches)
        try:
            cb = _make_before_llm_cb(session, mock_client)
            chat_ctx = _make_chat_ctx_with_user_message("What is the rent?")
            await cb(agent=MagicMock(), chat_ctx=chat_ctx)

            session.handle_caller_turn.assert_called_once_with("What is the rent?")
        finally:
            _stop_patches(patches)


# ---------------------------------------------------------------------------
# Test: Bug D regression — coordinator prompt survives rebuild
# ---------------------------------------------------------------------------


class TestBeforeLlmCbCoordinatorPrompt:
    """
    Bug D regression: coordinator prompt must be present in chat_ctx AFTER
    the callback completes. The prior bug wiped it because the system-prompt
    rebuild ran AFTER the coordinator append, stripping all system messages.
    """

    @pytest.mark.asyncio
    async def test_coordinator_prompt_present_after_callback(self) -> None:
        """
        The coordinator prompt (booking_agent_prompt) appended by before_llm_cb
        must still be in chat_ctx.messages after the callback returns.

        Before Bug D fix: rebuild stripped all system messages, including the
        just-appended coordinator prompt. After fix: rebuild runs first.
        """
        from voice_agent.worker.entrypoint import _make_before_llm_cb
        from voice_agent.tools.backend_client import BackendClient

        session = _make_mock_session()
        coordinator_text = "Please confirm the tour for Saturday at 10 AM."
        session.handle_caller_turn = AsyncMock(return_value={
            "escalated": False,
            "escalation_reason": None,
            "phase": "tour_booking",
            "booking_agent_prompt": coordinator_text,
            "email_agent_prompt": None,
        })
        mock_client = MagicMock(spec=BackendClient)

        patches = _PATCHES[:]
        _apply_patches(patches)
        try:
            cb = _make_before_llm_cb(session, mock_client)
            chat_ctx = _make_chat_ctx_with_user_message("Book me a tour for Saturday")
            await cb(agent=MagicMock(), chat_ctx=chat_ctx)

            # Both system prompt AND coordinator prompt must be present
            system_msgs = [m for m in chat_ctx.messages if m.role == "system"]
            assert len(system_msgs) >= 2, (
                f"Expected >= 2 system messages (base prompt + coordinator), "
                f"got {len(system_msgs)}: {system_msgs}"
            )

            # Find the coordinator instruction
            coordinator_msgs = [
                m for m in system_msgs
                if "COORDINATOR INSTRUCTION" in m.content
            ]
            assert len(coordinator_msgs) == 1, (
                "Coordinator prompt not found in chat_ctx — Bug D not fixed"
            )
            assert coordinator_text in coordinator_msgs[0].content
        finally:
            _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_system_prompt_comes_before_coordinator_prompt(self) -> None:
        """
        The base system prompt must be at index 0; coordinator prompt appended after.
        This ensures Gemini sees the safety rules before the coordinator instruction.
        """
        from voice_agent.worker.entrypoint import _make_before_llm_cb
        from voice_agent.tools.backend_client import BackendClient

        session = _make_mock_session()
        session.handle_caller_turn = AsyncMock(return_value={
            "escalated": False,
            "escalation_reason": None,
            "phase": "tour_booking",
            "booking_agent_prompt": "Confirm the booking",
            "email_agent_prompt": None,
        })
        mock_client = MagicMock(spec=BackendClient)

        patches = _PATCHES[:]
        _apply_patches(patches)
        try:
            cb = _make_before_llm_cb(session, mock_client)
            chat_ctx = _make_chat_ctx_with_user_message("Book Saturday")
            await cb(agent=MagicMock(), chat_ctx=chat_ctx)

            system_msgs = [m for m in chat_ctx.messages if m.role == "system"]
            assert len(system_msgs) >= 2
            # First system message = base prompt (no COORDINATOR)
            assert "COORDINATOR INSTRUCTION" not in system_msgs[0].content
            # Last system message = coordinator
            assert "COORDINATOR INSTRUCTION" in system_msgs[-1].content
        finally:
            _stop_patches(patches)


# ---------------------------------------------------------------------------
# Test: Medium 1 regression — escalation path rebuilds system prompt
# ---------------------------------------------------------------------------


class TestBeforeLlmCbEscalation:
    """
    Medium 1 regression: escalation path must rebuild the system prompt BEFORE
    returning. The prior bug returned early without rebuilding, leaving Gemini
    with a stale system prompt + escalation instruction on stale context.
    """

    @pytest.mark.asyncio
    async def test_escalation_path_includes_fresh_system_prompt(self) -> None:
        """
        When handle_caller_turn returns escalated=True, the chat_ctx must contain
        BOTH the rebuilt system prompt AND the escalation instruction.
        """
        from voice_agent.worker.entrypoint import _make_before_llm_cb
        from voice_agent.tools.backend_client import BackendClient

        session = _make_mock_session()
        session.handle_caller_turn = AsyncMock(return_value={
            "escalated": True,
            "escalation_reason": "FAIR_HOUSING",
            "phase": "escalation",
            "booking_agent_prompt": None,
            "email_agent_prompt": None,
        })
        mock_client = MagicMock(spec=BackendClient)

        patches = _PATCHES[:]
        _apply_patches(patches)
        try:
            cb = _make_before_llm_cb(session, mock_client)
            chat_ctx = _make_chat_ctx_with_user_message("Can you deny renters with kids?")
            await cb(agent=MagicMock(), chat_ctx=chat_ctx)

            system_msgs = [m for m in chat_ctx.messages if m.role == "system"]
            # Must have: base system prompt + escalation instruction
            assert len(system_msgs) >= 2, (
                f"Escalation path must rebuild system prompt. Got {len(system_msgs)} "
                f"system messages — Medium 1 not fixed"
            )

            escalation_msgs = [
                m for m in system_msgs
                if "IMMEDIATE ACTION REQUIRED" in m.content
            ]
            assert len(escalation_msgs) == 1, "Escalation instruction missing"
        finally:
            _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_escalation_system_prompt_comes_before_escalation_instruction(self) -> None:
        """
        Escalation: base system prompt at index 0, escalation instruction appended last.
        """
        from voice_agent.worker.entrypoint import _make_before_llm_cb
        from voice_agent.tools.backend_client import BackendClient

        session = _make_mock_session()
        session.handle_caller_turn = AsyncMock(return_value={
            "escalated": True,
            "escalation_reason": "FAIR_HOUSING",
            "phase": "escalation",
            "booking_agent_prompt": None,
            "email_agent_prompt": None,
        })
        mock_client = MagicMock(spec=BackendClient)

        patches = _PATCHES[:]
        _apply_patches(patches)
        try:
            cb = _make_before_llm_cb(session, mock_client)
            chat_ctx = _make_chat_ctx_with_user_message("Can I be refused for having a baby?")
            await cb(agent=MagicMock(), chat_ctx=chat_ctx)

            system_msgs = [m for m in chat_ctx.messages if m.role == "system"]
            assert len(system_msgs) >= 2
            assert "IMMEDIATE ACTION REQUIRED" not in system_msgs[0].content
            assert "IMMEDIATE ACTION REQUIRED" in system_msgs[-1].content
        finally:
            _stop_patches(patches)


# ---------------------------------------------------------------------------
# Test: Bug F regression — property name placeholder absent after profile load
# ---------------------------------------------------------------------------


class TestBeforeLlmCbPropertyProfile:
    """
    Bug F regression: system prompt must NOT contain [PROPERTY_NAME_PLACEHOLDER]
    when _property_profile has been loaded with real data.

    Before Bug F fix: _load_property_profile was never called in the production
    path (bridge_call_id branch), so the system prompt always contained the
    placeholder. After fix: the profile is loaded and passed to build_system_prompt.
    """

    @pytest.mark.asyncio
    async def test_system_prompt_contains_real_property_name(self) -> None:
        """
        When session._property_profile has a real property name, the system
        prompt must contain that name and NOT the placeholder string.
        """
        from voice_agent.worker.entrypoint import _make_before_llm_cb
        from voice_agent.tools.backend_client import BackendClient

        session = _make_mock_session(property_name="Riverside Lofts")
        mock_client = MagicMock(spec=BackendClient)

        patches = _PATCHES[:]
        _apply_patches(patches)
        try:
            cb = _make_before_llm_cb(session, mock_client)
            chat_ctx = _make_chat_ctx_empty()
            await cb(agent=MagicMock(), chat_ctx=chat_ctx)

            system_msgs = [m for m in chat_ctx.messages if m.role == "system"]
            assert len(system_msgs) >= 1
            system_text = system_msgs[0].content

            assert "[PROPERTY_NAME_PLACEHOLDER]" not in system_text, (
                "System prompt still contains placeholder — Bug F not fixed. "
                "_property_profile not being passed to build_system_prompt."
            )
            assert "Riverside Lofts" in system_text, (
                f"Expected 'Riverside Lofts' in system prompt, got: {system_text[:200]}"
            )
        finally:
            _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_system_prompt_placeholder_present_when_profile_default(self) -> None:
        """
        Sanity check: when _property_profile is the default (unloaded), the placeholder
        IS present. This confirms the test above is meaningful.
        """
        from voice_agent.worker.entrypoint import _make_before_llm_cb
        from voice_agent.tools.backend_client import BackendClient
        from voice_agent.prompts.system_prompt import PropertyProfileSlot

        session = _make_mock_session()
        # Override with the default (unloaded) profile
        session._property_profile = PropertyProfileSlot()
        mock_client = MagicMock(spec=BackendClient)

        patches = _PATCHES[:]
        _apply_patches(patches)
        try:
            cb = _make_before_llm_cb(session, mock_client)
            chat_ctx = _make_chat_ctx_empty()
            await cb(agent=MagicMock(), chat_ctx=chat_ctx)

            system_msgs = [m for m in chat_ctx.messages if m.role == "system"]
            assert len(system_msgs) >= 1
            system_text = system_msgs[0].content
            assert "[PROPERTY_NAME_PLACEHOLDER]" in system_text
        finally:
            _stop_patches(patches)


# ---------------------------------------------------------------------------
# Test: after_llm_cb — agent segment and flush
# ---------------------------------------------------------------------------


class TestAfterLlmCb:

    def _make_chat_ctx_with_agent_message(self, text: str) -> _MockChatContext:
        ctx = _MockChatContext()
        ctx.messages.append(_MockChatMessage(role="assistant", content=text))
        return ctx

    @pytest.mark.asyncio
    async def test_agent_segment_added_to_state(self) -> None:
        """after_llm_cb records the most recent assistant message in call state."""
        from voice_agent.worker.entrypoint import _make_after_llm_cb
        from voice_agent.tools.backend_client import BackendClient

        session = _make_mock_session()
        mock_client = MagicMock(spec=BackendClient)

        patches = _PATCHES[:]
        _apply_patches(patches)
        try:
            cb = _make_after_llm_cb(session, mock_client)
            agent_text = "The rent for a 1BR unit starts at $1,450 per month."
            chat_ctx = self._make_chat_ctx_with_agent_message(agent_text)

            await cb(agent=MagicMock(), chat_ctx=chat_ctx)

            # Agent segment must be in state
            from voice_agent.state.call_state import SpeakerRole
            agent_segs = [
                s for s in session.state.transcript
                if s.speaker == SpeakerRole.AGENT
            ]
            assert len(agent_segs) == 1
            assert agent_segs[0].text == agent_text
        finally:
            _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_transcript_flush_scheduled(self) -> None:
        """
        after_llm_cb schedules a _flush_segments_safe task. Verify it does not
        raise and that the flush function is eventually called (via asyncio.sleep(0)).
        """
        from voice_agent.worker.entrypoint import _make_after_llm_cb
        from voice_agent.tools.backend_client import BackendClient

        session = _make_mock_session()
        session._flush_transcript_segments = AsyncMock()  # type: ignore[method-assign]
        mock_client = MagicMock(spec=BackendClient)

        patches = _PATCHES[:]
        _apply_patches(patches)
        try:
            cb = _make_after_llm_cb(session, mock_client)
            chat_ctx = self._make_chat_ctx_with_agent_message("Glad to help!")

            await cb(agent=MagicMock(), chat_ctx=chat_ctx)

            # Let the background task run
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            session._flush_transcript_segments.assert_called_once()
        finally:
            _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_no_tool_called_event_for_plain_llm_response(self) -> None:
        """
        Medium 2 regression: after_llm_cb must NOT emit a tool_called event for
        a plain LLM response turn. The prior bug emitted one on every turn,
        flooding the dashboard with bogus events.
        """
        from voice_agent.worker.entrypoint import _make_after_llm_cb
        from voice_agent.tools.backend_client import BackendClient, CallEventType

        session = _make_mock_session()
        mock_client = MagicMock(spec=BackendClient)
        mock_client.create_call_event = AsyncMock(return_value=MagicMock())

        patches = _PATCHES[:]
        _apply_patches(patches)
        try:
            cb = _make_after_llm_cb(session, mock_client)
            chat_ctx = self._make_chat_ctx_with_agent_message("Our amenities include a pool.")

            await cb(agent=MagicMock(), chat_ctx=chat_ctx)

            # Let all background tasks run
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

            # create_call_event must NOT have been called with tool_called
            for call_args in mock_client.create_call_event.call_args_list:
                event_type = call_args.kwargs.get("event_type") or (
                    call_args.args[1] if len(call_args.args) > 1 else None
                )
                assert event_type != CallEventType.tool_called, (
                    f"after_llm_cb emitted tool_called event for a plain LLM response — "
                    f"Medium 2 not fixed. Call args: {call_args}"
                )
        finally:
            _stop_patches(patches)

    @pytest.mark.asyncio
    async def test_after_llm_cb_skips_when_no_backend_call_id(self) -> None:
        """
        after_llm_cb returns early if backend_call_id is not set.
        No segment should be added and no flush should be scheduled.
        """
        from voice_agent.worker.entrypoint import _make_after_llm_cb
        from voice_agent.tools.backend_client import BackendClient

        session = _make_mock_session()
        session.state.backend_call_id = None  # not set yet
        mock_client = MagicMock(spec=BackendClient)

        patches = _PATCHES[:]
        _apply_patches(patches)
        try:
            cb = _make_after_llm_cb(session, mock_client)
            chat_ctx = self._make_chat_ctx_with_agent_message("Hi there!")

            await cb(agent=MagicMock(), chat_ctx=chat_ctx)

            # No segments added
            assert len(session.state.transcript) == 0
        finally:
            _stop_patches(patches)
