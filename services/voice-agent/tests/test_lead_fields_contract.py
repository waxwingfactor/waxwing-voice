"""
Contract test: LeadFields (call_state) vs LeadFieldsInput (backend_client).

Verifies that the JSON payload produced by LeadFields.to_backend_payload()
can be re-parsed by the mirrored LeadFieldsInput schema without error.

Uses stable UUIDs from tests/fixtures/seed_property.json so any regression
in field alignment is immediately detectable against known seed data.

Stable UUIDs used:
  property_id  = 00000000-0000-0000-0000-000000000003  (Sunset Apartments)
  company_id   = 00000000-0000-0000-0000-000000000001
  call_id      = generated per test (not from seed)

These are hardcoded rather than loaded at runtime to keep tests fast and
dependency-free. They match seed_property.json exactly.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

import pytest

from voice_agent.state.call_state import LeadFields
from voice_agent.tools.backend_client import LeadFieldsInput

# ---------------------------------------------------------------------------
# Stable seed UUIDs (from tests/fixtures/seed_property.json)
# ---------------------------------------------------------------------------
SEED_PROPERTY_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")
SEED_COMPANY_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SEED_FIXTURE_PATH = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "seed_property.json"
)


# ---------------------------------------------------------------------------
# Fixture: load seed JSON and assert UUIDs match our constants
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seed_data() -> dict:
    """Load seed_property.json and verify UUIDs are stable."""
    assert SEED_FIXTURE_PATH.exists(), (
        f"Seed fixture not found at {SEED_FIXTURE_PATH}. "
        "Has Subbu's fixture commit landed?"
    )
    data = json.loads(SEED_FIXTURE_PATH.read_text(encoding="utf-8"))
    # Confirm the fixture's property UUID matches what we hardcoded above
    assert data["property"]["id"] == str(SEED_PROPERTY_ID), (
        "seed_property.json property UUID changed — update SEED_PROPERTY_ID constant"
    )
    assert data["company"]["id"] == str(SEED_COMPANY_ID), (
        "seed_property.json company UUID changed — update SEED_COMPANY_ID constant"
    )
    return data


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLeadFieldsContractVsBackend:
    """
    Each test:
    1. Builds a LeadFields (call_state) with realistic values.
    2. Calls to_backend_payload() to get the dict that would be sent to the API.
    3. Passes the dict to LeadFieldsInput.model_validate() (the mirrored backend
       schema in backend_client.py, kept in sync with voice_tools.py).
    4. Asserts round-trip equality on all included fields.

    If LeadFields and LeadFieldsInput ever diverge, model_validate() raises
    a ValidationError and the test fails with a clear field-level message.
    """

    def test_minimal_lead_fields_round_trips(self, seed_data):
        """A lead with only name+phone round-trips through LeadFieldsInput."""
        lf = LeadFields(
            name="Maria Garcia",
            phone="+15125550199",
        )
        payload = lf.to_backend_payload()

        validated = LeadFieldsInput.model_validate(payload)

        assert validated.name == "Maria Garcia"
        assert validated.phone == "+15125550199"
        # Fields not set must be None in validated output
        assert validated.email is None
        assert validated.budget is None
        assert validated.move_in_date is None

    def test_full_lead_fields_round_trips(self, seed_data):
        """All LeadFields fields that map to LeadFieldsInput round-trip cleanly."""
        lf = LeadFields(
            name="Jordan Lee",
            phone="+15125550177",
            email="jordan.lee@example.com",
            budget=2200.00,
            move_in_date=date(2026, 8, 1),
            desired_unit_type="2BR",
            pet_info={"type": "dog", "breed": "Labrador", "weight_lbs": 55},
            number_of_occupants=2,
            reason_for_moving="Job relocation",
            how_heard="Google search",
            urgency="high",
            tour_interest=True,
            lead_score="hot",
            # email_confirmed is local-only — must NOT appear in payload
            email_confirmed=True,
        )
        payload = lf.to_backend_payload()

        # email_confirmed must be stripped from backend payload
        assert "email_confirmed" not in payload, (
            "email_confirmed is a local-only field and must not be sent to backend"
        )

        validated = LeadFieldsInput.model_validate(payload)

        assert validated.name == "Jordan Lee"
        assert validated.phone == "+15125550177"
        assert validated.email == "jordan.lee@example.com"
        assert validated.budget == 2200.00
        assert validated.move_in_date == date(2026, 8, 1)
        assert validated.desired_unit_type == "2BR"
        assert validated.pet_info == {"type": "dog", "breed": "Labrador", "weight_lbs": 55}
        assert validated.number_of_occupants == 2
        assert validated.reason_for_moving == "Job relocation"
        assert validated.how_heard == "Google search"
        assert validated.urgency == "high"
        assert validated.tour_interest is True
        assert validated.lead_score == "hot"

    def test_old_draft_field_names_are_absent(self, seed_data):
        """
        Regression guard: fields from Akhil's draft that were renamed or removed
        must NOT appear in the payload sent to Harsha's backend.

        Draft -> Real schema mapping:
          phone_number          -> phone
          desired_move_in_date  -> move_in_date
          budget_min / budget_max -> budget  (single float)
          occupants             -> number_of_occupants
          preferred_contact_method -> (removed, not in Harsha's schema)
        """
        lf = LeadFields(
            phone="+15125550188",
            move_in_date=date(2026, 7, 1),
            budget=1800.00,
            number_of_occupants=1,
        )
        payload = lf.to_backend_payload()

        # Old field names that must NOT be present
        assert "phone_number" not in payload
        assert "desired_move_in_date" not in payload
        assert "budget_min" not in payload
        assert "budget_max" not in payload
        assert "occupants" not in payload
        assert "preferred_contact_method" not in payload

        # Real field names that MUST be present
        assert "phone" in payload
        assert "move_in_date" in payload
        assert "budget" in payload
        assert "number_of_occupants" in payload

    def test_urgency_values_match_harsha_enum(self, seed_data):
        """
        Harsha's LeadFieldsInput accepts: "low", "medium", "high", "immediate".
        Akhil's draft had: "asap", "this_month", "flexible" — these must NOT
        be used any more.
        """
        for valid_urgency in ("low", "medium", "high", "immediate"):
            lf = LeadFields(urgency=valid_urgency)
            payload = lf.to_backend_payload()
            # Should not raise
            validated = LeadFieldsInput.model_validate(payload)
            assert validated.urgency == valid_urgency

    def test_lead_score_values_match_harsha_enum(self, seed_data):
        """Harsha's lead_score: "hot", "warm", "cold" only."""
        for score in ("hot", "warm", "cold"):
            lf = LeadFields(lead_score=score)
            payload = lf.to_backend_payload()
            validated = LeadFieldsInput.model_validate(payload)
            assert validated.lead_score == score

    def test_seed_property_uuid_used_in_create_request(self, seed_data):
        """
        Demonstrate a complete create_or_update_lead request body using
        the stable seed property UUID. This is the shape BackendClient
        sends on the wire.
        """
        call_id = uuid.uuid4()

        lf = LeadFields(
            name="Seed Test Caller",
            phone="+15125550000",
            email="seed@sunsetapts.example",
            desired_unit_type="1BR",
            tour_interest=True,
            lead_score="warm",
        )

        request_body = {
            "property_id": str(SEED_PROPERTY_ID),
            "call_id": str(call_id),
            "lead_fields": lf.to_backend_payload(),
        }

        # property_id matches seed fixture
        assert request_body["property_id"] == "00000000-0000-0000-0000-000000000003"

        # lead_fields is valid per Harsha's schema
        validated = LeadFieldsInput.model_validate(request_body["lead_fields"])
        assert validated.name == "Seed Test Caller"
        assert validated.tour_interest is True
        assert validated.lead_score == "warm"

        # email_confirmed is NOT in the wire payload
        assert "email_confirmed" not in request_body["lead_fields"]

    def test_none_fields_excluded_from_payload(self, seed_data):
        """
        to_backend_payload() must exclude None values so partial updates
        don't overwrite existing backend data with null.
        """
        lf = LeadFields(name="Partial Update Only")
        payload = lf.to_backend_payload()

        assert "name" in payload
        # All other None fields must be absent
        for field in (
            "phone", "email", "budget", "move_in_date", "desired_unit_type",
            "pet_info", "number_of_occupants", "reason_for_moving", "how_heard",
            "urgency", "tour_interest", "lead_score",
        ):
            assert field not in payload, (
                f"None field '{field}' should not be in backend payload (partial update)"
            )
