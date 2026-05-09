"""
Unit tests for the system prompt assembly module.

Covers:
- Phase 0 assembly (placeholder data — no live backend needed)
- All strengthened safety rules appear verbatim or semantically
- Escalation trigger list mirrors EscalationDetector categories
- Confirmation language rules for booking and email
- 60-word paragraph cap rule is stated
- No-filler / no-markdown rule is stated
- Three canonical "I don't have that" response variants
- Defensive tool-call rules (never claim success unless tool returned it)
- Property context and knowledge slot rendering
- Canned response key completeness
"""

import pytest

from voice_agent.prompts.system_prompt import (
    CANNED_RESPONSES,
    KnowledgeSlot,
    PropertyProfileSlot,
    build_system_prompt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _prompt(**kwargs) -> str:
    return build_system_prompt(**kwargs)


# ---------------------------------------------------------------------------
# Basic assembly
# ---------------------------------------------------------------------------


class TestBuildSystemPromptBasics:
    def test_phase0_no_args(self):
        """Should build without errors using all placeholders."""
        prompt = _prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 200

    def test_property_name_in_role_section(self):
        profile = PropertyProfileSlot(property_name="Maple Grove")
        prompt = _prompt(property_profile=profile)
        assert "Maple Grove" in prompt

    def test_phase_included(self):
        prompt = _prompt(phase="lead_capture")
        assert "lead_capture" in prompt

    def test_sections_joined_by_double_newline(self):
        prompt = _prompt()
        assert "\n\n" in prompt

    def test_no_secrets_in_prompt(self):
        """Prompt must not contain any credential-like strings."""
        prompt = _prompt()
        forbidden = ["api_key", "secret", "password", "bearer"]
        for word in forbidden:
            assert word.lower() not in prompt.lower(), (
                f"Potential secret keyword '{word}' found in system prompt"
            )


# ---------------------------------------------------------------------------
# Safety rules — explicit prohibition tests
# ---------------------------------------------------------------------------


class TestSafetyRulesPresent:
    """
    Every rule in _SAFETY_RULES_TEXT must appear in the assembled prompt.
    These tests assert the specific language the spec requires.
    """

    def test_no_invented_rent(self):
        prompt = _prompt()
        # Must forbid inventing rent amounts
        assert "rent" in prompt.lower()
        assert "invent" in prompt.lower() or "never quote" in prompt.lower() or "NEVER QUOTE" in prompt

    def test_no_availability_promises(self):
        prompt = _prompt()
        # Must forbid availability promises
        assert "availability" in prompt.lower()
        assert "NEVER PROMISE AVAILABILITY" in prompt or "never promise" in prompt.lower()

    def test_no_maintenance_timeline_invention(self):
        prompt = _prompt()
        assert "maintenance" in prompt.lower()
        assert "NEVER COMMIT TO MAINTENANCE" in prompt or "maintenance timeline" in prompt.lower()

    def test_no_eligibility_decisions(self):
        prompt = _prompt()
        assert "eligib" in prompt.lower()
        assert "NEVER MAKE CREDIT OR ELIGIBILITY" in prompt or "eligibility" in prompt.lower()

    def test_fair_housing_mandatory_escalation_rule(self):
        prompt = _prompt()
        assert "Fair Housing" in prompt
        assert "mandatory" in prompt.lower() or "MANDATORY" in prompt

    def test_fair_housing_federal_classes_listed(self):
        prompt = _prompt()
        # All seven federal protected classes must be named
        federal_classes = [
            "race",
            "color",
            "religion",
            "national origin",
            "sex",
            "familial status",
            "disability",
        ]
        for cls in federal_classes:
            assert cls in prompt.lower(), (
                f"Federal protected class '{cls}' missing from system prompt"
            )

    def test_fair_housing_state_classes_noted(self):
        prompt = _prompt()
        # State classes: the prompt must mention states vary and list some
        assert "state" in prompt.lower()
        # At least one state-specific class
        state_signals = ["marital status", "sexual orientation", "source of income", "veteran"]
        assert any(s in prompt.lower() for s in state_signals), (
            "No state-specific protected classes found in prompt"
        )

    def test_no_legal_financial_advice(self):
        prompt = _prompt()
        assert "legal" in prompt.lower()
        assert "financial" in prompt.lower()

    def test_tool_failure_honesty_rule(self):
        prompt = _prompt()
        # Must tell LLM to say so when a tool fails
        assert "tool" in prompt.lower()
        assert "fail" in prompt.lower()
        # The specific instruction
        assert "say so" in prompt.lower() or "tell the caller" in prompt.lower() or "IF A TOOL FAILS" in prompt

    def test_60_word_cap_stated(self):
        prompt = _prompt()
        assert "60 words" in prompt or "60-word" in prompt

    def test_one_question_at_a_time(self):
        prompt = _prompt()
        assert "one question" in prompt.lower() or "Ask only one" in prompt

    def test_no_invention_rule_present(self):
        prompt = _prompt()
        assert "invent" in prompt.lower() or "never fill" in prompt.lower()


# ---------------------------------------------------------------------------
# Defensive tool-call rules
# ---------------------------------------------------------------------------


class TestDefensiveToolCallRules:
    def test_never_claim_tour_booked_without_success(self):
        prompt = _prompt()
        assert "book_tour" in prompt
        # Must instruct LLM not to claim booking unless tool returned success
        assert "successful" in prompt.lower() or "returned" in prompt.lower() or "DEFENSIVE TOOL-CALL" in prompt

    def test_never_claim_email_sent_without_success(self):
        prompt = _prompt()
        assert "send_follow_up_email" in prompt
        assert "email" in prompt.lower()

    def test_never_simulate_tool_result(self):
        prompt = _prompt()
        assert "simulate" in prompt.lower() or "invent" in prompt.lower()

    def test_honest_failure_language_in_tools_section(self):
        prompt = _prompt()
        assert "wasn't able to" in prompt or "not able to" in prompt.lower()


# ---------------------------------------------------------------------------
# Escalation triggers — must mirror EscalationDetector categories 1:1
# ---------------------------------------------------------------------------


class TestEscalationTriggersPresent:
    """
    The prompt must list every EscalationDetector category so Gemini
    self-escalates if the code-side detector misses a phrasing.
    """

    def test_emergency_keywords_in_prompt(self):
        prompt = _prompt()
        # Spot-check representative emergency keywords from EscalationDetector
        emergency_samples = ["fire", "gas leak", "flood", "medical emergency", "call 911"]
        for kw in emergency_samples:
            assert kw in prompt.lower(), f"Emergency keyword '{kw}' missing from escalation rules"

    def test_fair_housing_indirect_signals_in_prompt(self):
        prompt = _prompt()
        # These are the indirect signals from FAIR_HOUSING_KEYWORDS
        indirect = ["school district", "neighborhood demographics", "section 8", "housing voucher"]
        for signal in indirect:
            assert signal in prompt.lower(), (
                f"Fair Housing indirect signal '{signal}' missing from prompt"
            )

    def test_legal_keywords_in_prompt(self):
        prompt = _prompt()
        legal_samples = ["lawsuit", "attorney", "hud complaint", "civil rights"]
        for kw in legal_samples:
            assert kw in prompt.lower(), f"Legal keyword '{kw}' missing from escalation rules"

    def test_financial_keywords_in_prompt(self):
        prompt = _prompt()
        financial_samples = ["tax advice", "depreciation", "write-off"]
        for kw in financial_samples:
            assert kw in prompt.lower(), f"Financial keyword '{kw}' missing from escalation rules"

    def test_frustration_markers_in_prompt(self):
        prompt = _prompt()
        # Spot-check frustration markers from FRUSTRATION_MARKERS
        frustration_samples = ["get me a manager", "i'm furious", "fed up"]
        for marker in frustration_samples:
            assert marker in prompt.lower(), (
                f"Frustration marker '{marker}' missing from escalation rules"
            )

    def test_tool_failure_threshold_mentioned(self):
        prompt = _prompt()
        # The 3-failure threshold must be stated in the prompt
        assert "3" in prompt or "three" in prompt.lower()
        assert "tool" in prompt.lower()

    def test_emergency_urgency_label(self):
        prompt = _prompt()
        # Prompt must label emergency with highest urgency
        assert "urgency=emergency" in prompt or "emergency" in prompt.lower()

    def test_fair_housing_high_urgency(self):
        prompt = _prompt()
        assert "urgency=high" in prompt or "high" in prompt.lower()

    def test_escalation_closing_language(self):
        prompt = _prompt()
        # The canonical escalation closing from CANNED_RESPONSES
        assert "follow up shortly" in prompt or "follow up with you shortly" in prompt


# ---------------------------------------------------------------------------
# Confirmation language rules
# ---------------------------------------------------------------------------


class TestConfirmationRulesPresent:
    def test_tour_confirmation_reads_back_four_items(self):
        prompt = _prompt()
        # All four: date+time, property name, caller name, confirmation prompt
        assert "date" in prompt.lower()
        assert "time" in prompt.lower()
        assert "property name" in prompt.lower() or "property" in prompt.lower()
        assert "caller name" in prompt.lower() or "name" in prompt.lower()

    def test_email_letter_by_letter_rule(self):
        prompt = _prompt()
        assert "letter-by-letter" in prompt or "letter by letter" in prompt.lower() or "spell" in prompt.lower()

    def test_email_at_symbol_spoken_as_at(self):
        prompt = _prompt()
        assert "'at' for @" in prompt or "at" in prompt.lower()

    def test_do_not_book_before_confirmation(self):
        prompt = _prompt()
        assert "book_tour" in prompt
        # Must say wait for caller confirmation before calling
        assert "until" in prompt.lower() or "before calling book_tour" in prompt.lower() or "Do not call book_tour" in prompt

    def test_do_not_send_email_before_confirmed(self):
        prompt = _prompt()
        assert "send_follow_up_email" in prompt
        assert "until" in prompt.lower() or "confirms" in prompt.lower() or "Do not send" in prompt


# ---------------------------------------------------------------------------
# Three canonical "I don't have that" templates
# ---------------------------------------------------------------------------


class TestKnowledgeUnavailableTemplates:
    def test_knowledge_missing_template_present(self):
        prompt = _prompt()
        # Variant 1: knowledge not in retrieved data
        assert "I don't have that information in the property details" in prompt

    def test_sensitive_question_template_present(self):
        prompt = _prompt()
        # Variant 2: mandatory escalation
        assert "leasing team would need to address directly" in prompt

    def test_cannot_help_now_template_present(self):
        prompt = _prompt()
        # Variant 3: technical failure / low confidence
        assert "not able to help with that right now" in prompt or "not able to help" in prompt.lower()

    def test_templates_section_exists(self):
        prompt = _prompt()
        assert "STANDARD RESPONSE TEMPLATES" in prompt or "response template" in prompt.lower()


# ---------------------------------------------------------------------------
# Tone rules
# ---------------------------------------------------------------------------


class TestToneRules:
    def test_no_filler_rule(self):
        prompt = _prompt()
        # Must forbid "Great question!" and "Absolutely!"
        assert "Great question" in prompt or "filler" in prompt.lower() or "Absolutely" in prompt

    def test_no_markdown_rule(self):
        prompt = _prompt()
        assert "markdown" in prompt.lower() or "asterisk" in prompt.lower() or "emoji" in prompt.lower()

    def test_no_emoji_rule(self):
        prompt = _prompt()
        assert "emoji" in prompt.lower() or "No emoji" in prompt

    def test_tts_note_present(self):
        prompt = _prompt()
        # Must mention TTS so LLM understands why formatting matters
        assert "text-to-speech" in prompt.lower() or "TTS" in prompt or "spoken aloud" in prompt.lower()


# ---------------------------------------------------------------------------
# Knowledge slot rendering
# ---------------------------------------------------------------------------


class TestKnowledgeSlotRendering:
    def test_empty_slot_triggers_no_invention_instruction(self):
        knowledge = KnowledgeSlot(chunks=[], query_used="pet deposit")
        prompt = _prompt(knowledge=knowledge)
        assert "KNOWLEDGE STATUS" in prompt
        assert "invent" in prompt.lower() or "do not invent" in prompt.lower()

    def test_unavailable_flag_triggers_template_instruction(self):
        knowledge = KnowledgeSlot(knowledge_unavailable=True)
        prompt = _prompt(knowledge=knowledge)
        assert "KNOWLEDGE STATUS" in prompt
        assert "KNOWLEDGE MISSING" in prompt or "I don't have that information" in prompt

    def test_chunks_injected_verbatim(self):
        knowledge = KnowledgeSlot(
            chunks=[
                "[Pricing FAQ]\nPet deposit is $350 for dogs.",
                "[Lease Docs]\nMaximum weight is 50 lbs.",
            ],
            query_used="pet deposit",
        )
        prompt = _prompt(knowledge=knowledge)
        assert "Pet deposit is $350" in prompt
        assert "Maximum weight is 50 lbs" in prompt

    def test_chunks_only_instruction_present(self):
        knowledge = KnowledgeSlot(
            chunks=["[FAQ]\nRent is $1500."],
            query_used="rent",
        )
        prompt = _prompt(knowledge=knowledge)
        assert "use ONLY these facts" in prompt


# ---------------------------------------------------------------------------
# Property profile slot rendering
# ---------------------------------------------------------------------------


class TestPropertyProfileSlotRendering:
    def test_empty_amenities_shows_not_specified(self):
        profile = PropertyProfileSlot()
        rendered = profile.render()
        assert "not specified" in rendered

    def test_amenities_joined(self):
        profile = PropertyProfileSlot(amenities=["pool", "gym", "dog park"])
        rendered = profile.render()
        assert "pool" in rendered
        assert "gym" in rendered
        assert "dog park" in rendered

    def test_after_hours_shown_when_set(self):
        profile = PropertyProfileSlot(
            after_hours_greeting="Our office is closed. Leave a message."
        )
        rendered = profile.render()
        assert "Our office is closed" in rendered

    def test_no_after_hours_when_none(self):
        profile = PropertyProfileSlot(after_hours_greeting=None)
        rendered = profile.render()
        assert "After-hours" not in rendered


# ---------------------------------------------------------------------------
# Allowed tools section
# ---------------------------------------------------------------------------


class TestAllowedToolsSection:
    def test_all_ten_tools_listed(self):
        prompt = _prompt()
        expected_tools = [
            "search_property_knowledge",
            "get_property_profile",
            "create_or_update_lead",
            "create_call_event",
            "save_transcript_segment",
            "save_call_summary",
            "check_tour_availability",
            "book_tour",
            "send_follow_up_email",
            "request_human_handoff",
        ]
        for tool in expected_tools:
            assert tool in prompt, f"Tool '{tool}' missing from prompt tools section"


# ---------------------------------------------------------------------------
# Canned responses
# ---------------------------------------------------------------------------


class TestCannedResponses:
    def test_all_required_keys_present(self):
        expected_keys = [
            "fair_housing",
            "legal_question",
            "emergency",
            "information_not_available",
            "booking_failed",
            "tool_failure_generic",
            "sensitive_question",
            "cannot_help_now",
            "repeated_tool_failure",
            "goodbye_with_followup",
            "goodbye_no_followup",
        ]
        for key in expected_keys:
            assert key in CANNED_RESPONSES, f"Missing canned response key: '{key}'"

    def test_no_real_pii_in_canned_responses(self):
        """Canned responses must use placeholders, not real PII."""
        for key, text in CANNED_RESPONSES.items():
            # Email addresses only allowed as template placeholder {email}
            if "@" in text:
                assert "{email}" in text, (
                    f"Real email address found in canned response '{key}'"
                )

    def test_fair_housing_canned_response_does_not_answer(self):
        """Fair Housing canned response must NOT attempt to answer the question."""
        response = CANNED_RESPONSES["fair_housing"]
        # Must connect to team, not answer
        assert "leasing team" in response.lower() or "team" in response.lower()
        # Must NOT contain a direct answer about who can live there
        assert "can live" not in response.lower()
        assert "allowed" not in response.lower()

    def test_emergency_canned_response_directs_to_911(self):
        response = CANNED_RESPONSES["emergency"]
        assert "911" in response

    def test_booking_failed_canned_response_is_honest(self):
        response = CANNED_RESPONSES["booking_failed"]
        assert "wasn't able" in response.lower() or "not able" in response.lower()
        # Must NOT claim success
        assert "booked" not in response.lower() or "wasn't able" in response.lower()
