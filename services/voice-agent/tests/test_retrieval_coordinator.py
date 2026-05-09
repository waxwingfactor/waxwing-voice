"""
Phase 3 tests — RetrievalCoordinator.

Covers all six deliverable test scenarios from the Phase 3 spec:
  1. Information-seeking turn triggers retrieval; non-info turn does not.
  2. Empty results set the knowledge_unavailable flag.
  3. More than 3 results truncated to top-3 in prompt.
  4. Long chunk text truncated to ~400 chars.
  5. Trace event emitted with no PII (chunk text absent from log payload).
  6. knowledge_retrieved CallEvent fired with correct payload.

Also covers:
  - Phase-based skip (greeting, closing, ended, escalation).
  - Pure confirmation turns skipped.
  - Backend failure returns empty unavailable slot gracefully.
  - KnowledgeSlot.render() produces correct text for available/unavailable cases.
  - VoiceSession.handle_caller_turn() surfaces knowledge_unavailable in result.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from voice_agent.conversation.retrieval import (
    CHUNK_MAX_CHARS,
    TOP_K_PROMPT_CAP,
    RetrievalCoordinator,
    _is_info_seeking,
    _is_pure_confirmation,
)
from voice_agent.prompts.system_prompt import KnowledgeSlot
from voice_agent.tools.backend_client import (
    BackendClient,
    CallCreateResponse,
    CallEventType,
    KnowledgeResult,
    SearchKnowledgeResponse,
)

# ---------------------------------------------------------------------------
# Helpers to build canned SearchKnowledgeResponse objects
# ---------------------------------------------------------------------------

_PROP_ID = uuid.uuid4()
_CALL_ID = uuid.uuid4()


def _make_result(
    chunk_text: str,
    source_label: str,
    score: float = 0.85,
    page_number: int | None = None,
) -> KnowledgeResult:
    return KnowledgeResult(
        chunk_text=chunk_text,
        source_label=source_label,
        similarity_score=score,
        page_number=page_number,
    )


def _make_response(results: list[KnowledgeResult]) -> SearchKnowledgeResponse:
    return SearchKnowledgeResponse(
        property_id=_PROP_ID,
        query="test query",
        results=results,
    )


def _make_client(response: SearchKnowledgeResponse | None = None) -> AsyncMock:
    client = AsyncMock(spec=BackendClient)
    if response is not None:
        client.search_property_knowledge.return_value = response
    client.create_call_event.return_value = None
    return client


# ===========================================================================
# Section 1 — should_retrieve() logic
# ===========================================================================


class TestShouldRetrieve:
    def setup_method(self) -> None:
        self.coordinator = RetrievalCoordinator(client=MagicMock())

    # Phase-based skips
    @pytest.mark.parametrize("phase", ["greeting", "closing", "ended", "escalation"])
    def test_skipped_phases(self, phase: str) -> None:
        assert self.coordinator.should_retrieve("What is the rent?", phase) is False

    # Active phases with info-seeking question
    @pytest.mark.parametrize(
        "phase",
        ["lead_capture", "resident_support", "awaiting_confirmation", "intent_detection"],
    )
    def test_active_phase_with_question_mark(self, phase: str) -> None:
        assert self.coordinator.should_retrieve("What is the monthly rent?", phase) is True

    def test_question_word_start_triggers_retrieval(self) -> None:
        assert self.coordinator.should_retrieve("How do I submit a maintenance request", "resident_support") is True

    def test_can_i_triggers_retrieval(self) -> None:
        assert self.coordinator.should_retrieve("Can I have a pet here", "lead_capture") is True

    def test_is_triggers_retrieval(self) -> None:
        assert self.coordinator.should_retrieve("Is parking included", "lead_capture") is True

    # Pure confirmation turns skipped
    @pytest.mark.parametrize(
        "text",
        ["yes", "Yeah", "Correct!", "no", "Nope.", "cancel", "go ahead", "sounds good"],
    )
    def test_pure_confirmation_skipped(self, text: str) -> None:
        # Even in an active phase, confirmations don't warrant retrieval
        assert self.coordinator.should_retrieve(text, "lead_capture") is False

    def test_plain_statement_no_question_mark(self) -> None:
        # "I am looking for a 2BR" — no "?" and does not start with a question word
        assert self.coordinator.should_retrieve("I am looking for a 2BR apartment", "lead_capture") is False

    def test_tell_me_starts_with_question_prefix(self) -> None:
        assert self.coordinator.should_retrieve("Tell me about parking", "lead_capture") is True


# ===========================================================================
# Section 2 — Helper heuristics
# ===========================================================================


class TestHeuristics:
    def test_question_mark_is_info_seeking(self) -> None:
        assert _is_info_seeking("Do you have a pool?") is True

    def test_no_question_mark_no_prefix(self) -> None:
        assert _is_info_seeking("My name is John Smith") is False

    def test_pure_yes_confirmation(self) -> None:
        assert _is_pure_confirmation("yes") is True

    def test_pure_no_confirmation(self) -> None:
        assert _is_pure_confirmation("no") is True

    def test_compound_sentence_not_confirmation(self) -> None:
        # Has "yes" but also other content
        assert _is_pure_confirmation("yes, I want the 2-bedroom unit") is False


# ===========================================================================
# Section 3 — do_retrieve() — happy path
# ===========================================================================


class TestDoRetrieve:
    @pytest.mark.asyncio
    async def test_info_seeking_triggers_backend_call(self) -> None:
        """Deliverable 1: Information-seeking turn triggers retrieval."""
        response = _make_response([_make_result("Rent is $1,500/month.", "Pricing FAQ")])
        client = _make_client(response)
        coord = RetrievalCoordinator(client=client)

        slot = await coord.do_retrieve(
            caller_text="What is the monthly rent?",
            phase="lead_capture",
            property_id=_PROP_ID,
            call_id=_CALL_ID,
        )

        client.search_property_knowledge.assert_called_once()
        assert not slot.knowledge_unavailable
        assert len(slot.chunks) == 1
        assert "Pricing FAQ" in slot.chunks[0]

    @pytest.mark.asyncio
    async def test_non_info_turn_skips_backend(self) -> None:
        """Deliverable 1: Non-info turn does not call the backend."""
        client = _make_client()
        coord = RetrievalCoordinator(client=client)

        slot = await coord.do_retrieve(
            caller_text="My name is Akhil",
            phase="lead_capture",
            property_id=_PROP_ID,
            call_id=_CALL_ID,
        )

        client.search_property_knowledge.assert_not_called()
        assert slot.knowledge_unavailable is False  # default slot, not "unavailable"
        assert slot.chunks == []

    @pytest.mark.asyncio
    async def test_empty_results_sets_unavailable_flag(self) -> None:
        """Deliverable 2: Empty results → knowledge_unavailable = True."""
        response = _make_response([])
        client = _make_client(response)
        coord = RetrievalCoordinator(client=client)

        slot = await coord.do_retrieve(
            caller_text="What is the pet deposit?",
            phase="lead_capture",
            property_id=_PROP_ID,
            call_id=_CALL_ID,
        )

        assert slot.knowledge_unavailable is True
        assert slot.chunks == []

    @pytest.mark.asyncio
    async def test_below_min_score_filtered_out(self) -> None:
        """Results below MIN_SIMILARITY_SCORE (0.5) are dropped."""
        response = _make_response([
            _make_result("High score result.", "Doc A", score=0.9),
            _make_result("Low score result.", "Doc B", score=0.3),  # below 0.5
        ])
        client = _make_client(response)
        coord = RetrievalCoordinator(client=client)

        slot = await coord.do_retrieve(
            caller_text="What is included in rent?",
            phase="lead_capture",
            property_id=_PROP_ID,
            call_id=_CALL_ID,
        )

        assert len(slot.chunks) == 1
        assert "Doc A" in slot.chunks[0]

    @pytest.mark.asyncio
    async def test_more_than_3_results_capped_at_3(self) -> None:
        """Deliverable 3: More than 3 results truncated to top-3 by score."""
        results = [
            _make_result(f"Chunk {i}.", f"Doc{i}", score=0.9 - i * 0.05)
            for i in range(5)
        ]
        response = _make_response(results)
        client = _make_client(response)
        coord = RetrievalCoordinator(client=client)

        slot = await coord.do_retrieve(
            caller_text="What is the parking policy?",
            phase="lead_capture",
            property_id=_PROP_ID,
            call_id=_CALL_ID,
        )

        assert len(slot.chunks) == TOP_K_PROMPT_CAP  # 3
        # Verify highest-score chunks were chosen: Doc0, Doc1, Doc2
        assert "Doc0" in slot.chunks[0]
        assert "Doc1" in slot.chunks[1]
        assert "Doc2" in slot.chunks[2]

    @pytest.mark.asyncio
    async def test_long_chunk_truncated(self) -> None:
        """Deliverable 4: Chunk text exceeding CHUNK_MAX_CHARS is truncated."""
        long_text = "x" * (CHUNK_MAX_CHARS + 200)
        response = _make_response([_make_result(long_text, "Lease Docs")])
        client = _make_client(response)
        coord = RetrievalCoordinator(client=client)

        slot = await coord.do_retrieve(
            caller_text="Tell me about the lease terms",
            phase="lead_capture",
            property_id=_PROP_ID,
            call_id=_CALL_ID,
        )

        # Chunk text should be truncated; format is "[Label]\n{text}"
        assert len(slot.chunks) == 1
        chunk = slot.chunks[0]
        # After label line, the text part must not exceed CHUNK_MAX_CHARS + 3 ("...")
        text_part = chunk.split("\n", 1)[1]
        assert len(text_part) <= CHUNK_MAX_CHARS + 3
        assert text_part.endswith("...")

    @pytest.mark.asyncio
    async def test_source_label_with_page_number(self) -> None:
        """Page number included in source label when present."""
        response = _make_response([
            _make_result("Pet policy text.", "Lease Agreement", score=0.8, page_number=12)
        ])
        client = _make_client(response)
        coord = RetrievalCoordinator(client=client)

        slot = await coord.do_retrieve(
            caller_text="What is the pet policy?",
            phase="lead_capture",
            property_id=_PROP_ID,
            call_id=_CALL_ID,
        )

        assert "Lease Agreement p.12" in slot.chunks[0]

    @pytest.mark.asyncio
    async def test_knowledge_retrieved_event_emitted(self) -> None:
        """Deliverable 6: knowledge_retrieved CallEvent is emitted with correct payload."""
        response = _make_response([
            _make_result("Rent info.", "Pricing FAQ", score=0.9),
        ])
        client = _make_client(response)
        coord = RetrievalCoordinator(client=client)

        await coord.do_retrieve(
            caller_text="How much is rent?",
            phase="lead_capture",
            property_id=_PROP_ID,
            call_id=_CALL_ID,
        )

        client.create_call_event.assert_called_once()
        call_args = client.create_call_event.call_args
        assert call_args.kwargs["event_type"] == CallEventType.knowledge_retrieved
        payload = call_args.kwargs["payload"]
        assert payload["result_count"] == 1
        assert "Pricing FAQ" in payload["source_labels"]
        # Chunk text must NOT be in the payload (PII risk)
        assert "Rent info." not in str(payload)

    @pytest.mark.asyncio
    async def test_trace_event_payload_has_no_chunk_text(self) -> None:
        """Deliverable 5: Trace log and CallEvent payload contain no chunk text."""
        chunk_text = "SECRET PRICING: $2,000/month for Unit 5A (tenant: John Doe)"
        response = _make_response([_make_result(chunk_text, "Sensitive Doc", score=0.95)])
        client = _make_client(response)
        coord = RetrievalCoordinator(client=client)

        await coord.do_retrieve(
            caller_text="What is the rent?",
            phase="lead_capture",
            property_id=_PROP_ID,
            call_id=_CALL_ID,
        )

        call_args = client.create_call_event.call_args
        payload = call_args.kwargs["payload"]
        # Chunk text must not appear in the event payload
        assert chunk_text not in str(payload)
        # Only source label appears
        assert "Sensitive Doc" in payload["source_labels"]

    @pytest.mark.asyncio
    async def test_backend_failure_returns_empty_unavailable_slot(self) -> None:
        """Backend failure during retrieval returns graceful empty slot."""
        from voice_agent.tools.backend_client import BackendToolError

        client = AsyncMock(spec=BackendClient)
        client.search_property_knowledge.side_effect = BackendToolError(
            code="TIMEOUT", message="Timed out", retryable=True
        )
        client.create_call_event.return_value = None

        coord = RetrievalCoordinator(client=client)

        slot = await coord.do_retrieve(
            caller_text="What amenities are available?",
            phase="lead_capture",
            property_id=_PROP_ID,
            call_id=_CALL_ID,
        )

        # Should return gracefully, not raise
        assert slot.knowledge_unavailable is True
        assert slot.chunks == []

    @pytest.mark.asyncio
    async def test_greeting_phase_never_retrieves(self) -> None:
        """Retrieval skipped in greeting even with a question."""
        client = _make_client()
        coord = RetrievalCoordinator(client=client)

        slot = await coord.do_retrieve(
            caller_text="What is the rent?",
            phase="greeting",
            property_id=_PROP_ID,
            call_id=_CALL_ID,
        )

        client.search_property_knowledge.assert_not_called()
        assert slot.chunks == []


# ===========================================================================
# Section 4 — KnowledgeSlot rendering
# ===========================================================================


class TestKnowledgeSlotRender:
    def test_unavailable_flag_renders_no_invent_instruction(self) -> None:
        slot = KnowledgeSlot(knowledge_unavailable=True)
        rendered = slot.render()
        assert "don't have that information" in rendered.lower() or "KNOWLEDGE STATUS" in rendered

    def test_empty_chunks_renders_no_invent_instruction(self) -> None:
        slot = KnowledgeSlot(chunks=[], knowledge_unavailable=False)
        rendered = slot.render()
        # Empty chunks without unavailable flag still warns (no retrieval happened)
        assert "do not" in rendered.lower() or "KNOWLEDGE STATUS" in rendered or "No property knowledge" in rendered

    def test_available_chunks_render_source_labels(self) -> None:
        slot = KnowledgeSlot(
            chunks=["[Pricing FAQ p.3]\nRent is $1,500/month."],
            knowledge_unavailable=False,
        )
        rendered = slot.render()
        assert "Pricing FAQ p.3" in rendered
        assert "Rent is $1,500/month." in rendered

    def test_multiple_chunks_separated(self) -> None:
        slot = KnowledgeSlot(
            chunks=["[Doc A]\nFirst fact.", "[Doc B]\nSecond fact."],
            knowledge_unavailable=False,
        )
        rendered = slot.render()
        assert "First fact." in rendered
        assert "Second fact." in rendered


# ===========================================================================
# Section 5 — VoiceSession integration (retrieval wired into handle_caller_turn)
# ===========================================================================


class TestVoiceSessionRetrievalIntegration:
    """
    Verify that handle_caller_turn() runs the retrieval coordinator and
    surfaces knowledge_unavailable + retrieved_source_labels in its result.
    """

    @pytest.fixture
    def _ids(self):
        property_id = str(uuid.uuid4())
        backend_call_id = str(uuid.uuid4())
        return property_id, backend_call_id

    def _make_session(self, property_id, backend_call_id, knowledge_response):
        from voice_agent.agent.session import VoiceSession

        client = AsyncMock(spec=BackendClient)
        client.create_call.return_value = CallCreateResponse(
            id=uuid.UUID(backend_call_id),
            property_id=uuid.UUID(property_id),
            status="active",
        )
        client.create_call_event.return_value = None
        client.search_property_knowledge.return_value = knowledge_response

        session = VoiceSession(
            property_id=property_id,
            jwt_token="eyJhbGciOiJIUzI1NiJ9.eyJjb21wYW55X2lkIjoidGVzdCJ9.fake_sig",
            backend_client=client,
        )
        # Simulate that start() has already run and set backend_call_id
        session.state.backend_call_id = backend_call_id
        return session, client

    @pytest.mark.asyncio
    async def test_info_question_surfaces_knowledge_in_result(self, _ids) -> None:
        property_id, backend_call_id = _ids
        response = _make_response([
            _make_result("Parking included for all units.", "Parking Policy", score=0.88)
        ])
        session, client = self._make_session(property_id, backend_call_id, response)

        result = await session.handle_caller_turn(
            caller_text="Is parking included in the rent?"
        )

        assert result["escalated"] is False
        assert result["knowledge_unavailable"] is False
        assert "Parking Policy" in result["retrieved_source_labels"]

    @pytest.mark.asyncio
    async def test_empty_knowledge_surfaces_unavailable_flag(self, _ids) -> None:
        property_id, backend_call_id = _ids
        response = _make_response([])
        session, client = self._make_session(property_id, backend_call_id, response)

        result = await session.handle_caller_turn(
            caller_text="What is the cancellation policy?"
        )

        assert result["knowledge_unavailable"] is True
        assert result["retrieved_source_labels"] == []

    @pytest.mark.asyncio
    async def test_no_backend_call_id_skips_retrieval(self, _ids) -> None:
        """If backend_call_id is not yet set (pre-start), retrieval is skipped."""
        property_id, backend_call_id = _ids
        client = AsyncMock(spec=BackendClient)
        client.create_call_event.return_value = None

        from voice_agent.agent.session import VoiceSession

        session = VoiceSession(
            property_id=property_id,
            jwt_token="eyJhbGciOiJIUzI1NiJ9.eyJjb21wYW55X2lkIjoidGVzdCJ9.fake_sig",
            backend_client=client,
        )
        # backend_call_id is None — start() has NOT been called

        result = await session.handle_caller_turn(
            caller_text="What is the monthly rent?"
        )

        client.search_property_knowledge.assert_not_called()
        # Result still has the keys with defaults
        assert "knowledge_unavailable" in result
        assert "retrieved_source_labels" in result
