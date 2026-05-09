"""
Phase 4 tests — FollowUpEmailCoordinator.

Covers all spec deliverables from §9:
  1. Email-spelling readback: "akhil@2ispeed.com" → letter-by-letter format.
  2. Caller corrects email → captured field updated, confirmation re-asked.
  3. send_follow_up_email not called if email_confirmed=False.
  4. Template selection: tour booked → tour_confirmation; no tour → follow_up.
  5. Send failure (retryable) → retried once.
  6. Send failure (non-retryable) → escalated to handoff.

Also covers:
  - format_email_for_readback utility function.
  - _extract_email_from_text helper.
  - start() generates correct first prompt.
  - VoiceSession.try_enter_email_followup eligibility gate.
  - VoiceSession.handle_caller_turn delegation to email coordinator.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from voice_agent.conversation.email_followup import (
    EmailSubState,
    FollowUpEmailCoordinator,
    _extract_email_from_text,
    _spell_domain,
    _spell_segment,
    format_email_for_readback,
)
from voice_agent.tools.backend_client import (
    BackendClient,
    BackendToolError,
    CallCreateResponse,
    EmailTemplateType,
    SendFollowUpEmailResponse,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROP_ID = uuid.uuid4()
_CALL_ID = uuid.uuid4()
_LEAD_ID = uuid.uuid4()


def _make_send_response() -> SendFollowUpEmailResponse:
    return SendFollowUpEmailResponse(
        email_id=uuid.uuid4(),
        recipient="[redacted]",
        subject="Tour Confirmation",
        delivery_status="queued",
    )


def _make_client(send_response: SendFollowUpEmailResponse | None = None,
                 send_side_effect: Exception | None = None) -> AsyncMock:
    client = AsyncMock(spec=BackendClient)
    if send_response is not None:
        client.send_follow_up_email.return_value = send_response
    if send_side_effect is not None:
        client.send_follow_up_email.side_effect = send_side_effect
    return client


def _make_coord(send_response=None, send_side_effect=None) -> FollowUpEmailCoordinator:
    client = _make_client(send_response=send_response, send_side_effect=send_side_effect)
    coord = FollowUpEmailCoordinator(client=client)
    return coord


# ===========================================================================
# Section 1 — Email readback formatting
# ===========================================================================


class TestEmailReadback:
    def test_basic_email_readback(self) -> None:
        """Deliverable 1: akhil@2ispeed.com → expected letter-by-letter format."""
        result = format_email_for_readback("akhil@2ispeed.com")
        # Should contain "at" for "@"
        assert " at " in result
        # Should contain "dot" for "."
        assert " dot " in result
        # Individual letters of "akhil" should appear separated by hyphens
        assert "a-k-h-i-l" in result

    def test_simple_email(self) -> None:
        result = format_email_for_readback("bob@gmail.com")
        assert " at " in result
        assert "dot" in result
        assert "b-o-b" in result

    def test_email_with_numbers(self) -> None:
        result = format_email_for_readback("user2@example.org")
        assert " at " in result
        assert "2" in result

    def test_spell_segment_single_word(self) -> None:
        assert _spell_segment("abc") == "a-b-c"

    def test_spell_segment_with_numbers(self) -> None:
        assert _spell_segment("ab2") == "a-b-2"

    def test_spell_domain_simple(self) -> None:
        result = _spell_domain("gmail.com")
        assert "dot" in result
        assert "g-m-a-i-l" in result
        assert "c-o-m" in result

    def test_no_at_sign_spells_whole_thing(self) -> None:
        result = format_email_for_readback("notanemail")
        assert "-" in result  # spells it out
        assert " at " not in result

    def test_start_method_includes_readback(self) -> None:
        """start() returns a prompt that includes the spelled-out email."""
        coord = _make_coord(send_response=_make_send_response())
        result = coord.start(email="akhil@2ispeed.com", booking_confirmed=False)
        assert result.agent_prompt is not None
        assert "a-k-h-i-l" in result.agent_prompt
        assert " at " in result.agent_prompt


# ===========================================================================
# Section 2 — Email correction flow
# ===========================================================================


class TestEmailCorrection:
    @pytest.mark.asyncio
    async def test_caller_rejects_email_enters_correction_state(self) -> None:
        """Deliverable 2: Caller says 'no' → enter AWAITING_CORRECTION."""
        coord = _make_coord(send_response=_make_send_response())
        coord.start(email="wrong@domain.com", booking_confirmed=False)

        result = await coord.handle_turn("no, that's not right", _CALL_ID, _PROP_ID, _LEAD_ID)
        assert result.sub_state == EmailSubState.AWAITING_CORRECTION
        assert not result.done

    @pytest.mark.asyncio
    async def test_caller_provides_corrected_email_re_reads_back(self) -> None:
        """After correction, coordinator re-reads the new address."""
        coord = _make_coord(send_response=_make_send_response())
        coord.start(email="bad@domain.com", booking_confirmed=False)

        # Reject current email
        await coord.handle_turn("no", _CALL_ID, _PROP_ID, _LEAD_ID)

        # Provide correction
        result = await coord.handle_turn("it is akhil@2ispeed.com", _CALL_ID, _PROP_ID, _LEAD_ID)
        assert result.sub_state == EmailSubState.CONFIRMING_EMAIL
        assert result.agent_prompt is not None
        # Should read back the corrected email
        assert "2ispeed" in result.agent_prompt or "a-k-h-i-l" in result.agent_prompt

    @pytest.mark.asyncio
    async def test_send_not_called_before_confirmation(self) -> None:
        """Deliverable 3: send_follow_up_email NOT called if email_confirmed=False."""
        client = _make_client(send_response=_make_send_response())
        coord = FollowUpEmailCoordinator(client=client)
        coord.start(email="test@example.com", booking_confirmed=False)

        # Reject — this keeps email_confirmed=False
        await coord.handle_turn("no, it's wrong", _CALL_ID, _PROP_ID, _LEAD_ID)

        client.send_follow_up_email.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirmation_then_send_called(self) -> None:
        """Confirming email → send_follow_up_email called exactly once."""
        client = _make_client(send_response=_make_send_response())
        coord = FollowUpEmailCoordinator(client=client)
        coord.start(email="test@example.com", booking_confirmed=False)

        result = await coord.handle_turn("yes, that's right", _CALL_ID, _PROP_ID, _LEAD_ID)
        assert result.email_confirmed
        assert result.email_sent
        client.send_follow_up_email.assert_called_once()


# ===========================================================================
# Section 3 — Template selection
# ===========================================================================


class TestTemplateSelection:
    @pytest.mark.asyncio
    async def test_tour_booked_uses_tour_confirmation_template(self) -> None:
        """Deliverable 4: booking_confirmed=True → tour_confirmation template."""
        client = _make_client(send_response=_make_send_response())
        coord = FollowUpEmailCoordinator(client=client)
        coord.start(email="user@example.com", booking_confirmed=True)

        await coord.handle_turn("yes", _CALL_ID, _PROP_ID, _LEAD_ID)

        call_kwargs = client.send_follow_up_email.call_args.kwargs
        assert call_kwargs["template_type"] == EmailTemplateType.tour_confirmation

    @pytest.mark.asyncio
    async def test_no_tour_uses_follow_up_template(self) -> None:
        """Deliverable 4: booking_confirmed=False → follow_up template."""
        client = _make_client(send_response=_make_send_response())
        coord = FollowUpEmailCoordinator(client=client)
        coord.start(email="user@example.com", booking_confirmed=False)

        await coord.handle_turn("yes", _CALL_ID, _PROP_ID, _LEAD_ID)

        call_kwargs = client.send_follow_up_email.call_args.kwargs
        assert call_kwargs["template_type"] == EmailTemplateType.follow_up


# ===========================================================================
# Section 4 — Retryable failure
# ===========================================================================


class TestRetryableFailure:
    @pytest.mark.asyncio
    async def test_retryable_failure_retries_once(self) -> None:
        """Deliverable 5: Retryable send failure → retried once."""
        client = _make_client(
            send_side_effect=BackendToolError(
                code="SERVICE_UNAVAILABLE", message="Down", retryable=True
            )
        )
        coord = FollowUpEmailCoordinator(client=client)
        coord.start(email="user@example.com", booking_confirmed=False)

        result = await coord.handle_turn("yes", _CALL_ID, _PROP_ID, _LEAD_ID)

        assert result.escalate
        assert not result.email_sent
        # Called twice (attempt 1 + retry)
        assert client.send_follow_up_email.call_count == 2

    @pytest.mark.asyncio
    async def test_retryable_failure_succeeds_on_retry(self) -> None:
        """If second attempt succeeds, email_sent=True."""
        client = AsyncMock(spec=BackendClient)
        client.send_follow_up_email.side_effect = [
            BackendToolError("TIMEOUT", "Timed out", retryable=True),
            _make_send_response(),
        ]
        coord = FollowUpEmailCoordinator(client=client)
        coord.start(email="retry@example.com", booking_confirmed=False)

        result = await coord.handle_turn("yes", _CALL_ID, _PROP_ID, _LEAD_ID)

        assert result.email_sent
        assert not result.escalate
        assert client.send_follow_up_email.call_count == 2

    @pytest.mark.asyncio
    async def test_never_claims_sent_on_failure(self) -> None:
        """Safety: email_sent must be False when send fails."""
        client = _make_client(
            send_side_effect=BackendToolError("ERR", "fail", retryable=False)
        )
        coord = FollowUpEmailCoordinator(client=client)
        coord.start(email="fail@example.com", booking_confirmed=False)

        result = await coord.handle_turn("yes", _CALL_ID, _PROP_ID, _LEAD_ID)
        assert not result.email_sent


# ===========================================================================
# Section 5 — Non-retryable failure
# ===========================================================================


class TestNonRetryableFailure:
    @pytest.mark.asyncio
    async def test_non_retryable_escalates_immediately(self) -> None:
        """Deliverable 6: Non-retryable failure → escalate, no retry."""
        client = _make_client(
            send_side_effect=BackendToolError(
                code="LEAD_NOT_FOUND", message="Lead missing", retryable=False
            )
        )
        coord = FollowUpEmailCoordinator(client=client)
        coord.start(email="user@example.com", booking_confirmed=True)

        result = await coord.handle_turn("yes, correct", _CALL_ID, _PROP_ID, _LEAD_ID)

        assert result.escalate
        assert not result.email_sent
        assert client.send_follow_up_email.call_count == 1  # no retry

    @pytest.mark.asyncio
    async def test_escalation_reason_set_on_failure(self) -> None:
        client = _make_client(
            send_side_effect=BackendToolError("ERR", "fail", retryable=False)
        )
        coord = FollowUpEmailCoordinator(client=client)
        coord.start(email="u@e.com", booking_confirmed=False)
        result = await coord.handle_turn("yes", _CALL_ID, _PROP_ID, _LEAD_ID)
        assert result.escalation_reason is not None


# ===========================================================================
# Section 6 — Utility: _extract_email_from_text
# ===========================================================================


class TestExtractEmail:
    def test_extracts_email_from_sentence(self) -> None:
        assert _extract_email_from_text("my email is john@example.com") == "john@example.com"

    def test_extracts_email_trailing_punctuation(self) -> None:
        result = _extract_email_from_text("use john@example.com.")
        assert result == "john@example.com"

    def test_returns_none_when_no_email(self) -> None:
        assert _extract_email_from_text("I don't know my email") is None

    def test_returns_none_for_at_without_domain(self) -> None:
        # "@" exists but no "." in the domain part
        assert _extract_email_from_text("call me @twitter") is None


# ===========================================================================
# Section 7 — VoiceSession integration: try_enter_email_followup
# ===========================================================================


class TestVoiceSessionEmailFollowupIntegration:
    def _make_session(self):
        from voice_agent.agent.session import VoiceSession

        prop_id = str(uuid.uuid4())
        backend_call_id = str(uuid.uuid4())
        lead_id = str(uuid.uuid4())

        client = AsyncMock(spec=BackendClient)
        client.create_call.return_value = CallCreateResponse(
            id=uuid.UUID(backend_call_id),
            property_id=uuid.UUID(prop_id),
            status="active",
        )
        client.send_follow_up_email.return_value = _make_send_response()
        client.create_call_event.return_value = None
        client.search_property_knowledge.return_value = None

        session = VoiceSession(
            property_id=prop_id,
            jwt_token="eyJhbGciOiJIUzI1NiJ9.eyJjb21wYW55X2lkIjoidGVzdCJ9.fake_sig",
            backend_client=client,
        )
        session.state.backend_call_id = backend_call_id
        session.state.lead_id = lead_id
        return session, client

    def test_try_enter_email_followup_eligible_with_tour(self) -> None:
        session, _ = self._make_session()
        session.state.lead_fields.email = "test@example.com"
        session.state.booking_confirmed = True
        from voice_agent.conversation.state_machine import ConversationPhase
        session.state_machine.force_phase(ConversationPhase.CLOSING)

        result = session.try_enter_email_followup()
        assert result is True
        assert session.state_machine.phase == ConversationPhase.EMAIL_FOLLOWUP

    def test_try_enter_email_followup_ineligible_no_email(self) -> None:
        session, _ = self._make_session()
        session.state.lead_fields.email = None
        session.state.booking_confirmed = True
        from voice_agent.conversation.state_machine import ConversationPhase
        session.state_machine.force_phase(ConversationPhase.CLOSING)

        result = session.try_enter_email_followup()
        assert result is False

    def test_try_enter_email_followup_ineligible_no_booking_no_interest(self) -> None:
        session, _ = self._make_session()
        session.state.lead_fields.email = "test@example.com"
        session.state.booking_confirmed = False
        session.state.lead_fields.tour_interest = None  # no interest captured
        from voice_agent.conversation.state_machine import ConversationPhase
        session.state_machine.force_phase(ConversationPhase.CLOSING)

        result = session.try_enter_email_followup()
        assert result is False

    @pytest.mark.asyncio
    async def test_email_confirmation_turn_via_session(self) -> None:
        """handle_caller_turn() in EMAIL_FOLLOWUP delegates to email coordinator."""
        from voice_agent.conversation.state_machine import ConversationPhase

        session, client = self._make_session()
        session.state.lead_fields.email = "akhil@2ispeed.com"
        session.state.booking_confirmed = True
        session.state_machine.force_phase(ConversationPhase.EMAIL_FOLLOWUP)
        # Start the coordinator manually (as VoiceSession.try_enter_email_followup would)
        session._email_coordinator.start(
            email="akhil@2ispeed.com",
            booking_confirmed=True,
        )

        # Caller confirms the email
        result = await session.handle_caller_turn("yes, that's correct")

        assert result.get("email_confirmed") is True or result.get("email_sent") is True

    @pytest.mark.asyncio
    async def test_send_not_called_if_email_confirmed_false(self) -> None:
        """Safety: send_follow_up_email must not be called before confirmation."""
        from voice_agent.conversation.state_machine import ConversationPhase

        session, client = self._make_session()
        session.state.lead_fields.email = "test@example.com"
        session.state.booking_confirmed = False
        session.state_machine.force_phase(ConversationPhase.EMAIL_FOLLOWUP)
        session._email_coordinator.start(
            email="test@example.com",
            booking_confirmed=False,
        )

        # Caller rejects email — send must NOT be called
        await session.handle_caller_turn("no, that's not my email")
        client.send_follow_up_email.assert_not_called()
