"""Initial schema — companies, users, properties, calls.

Leads, bookings, email_records, documents, knowledge_chunks, and audit_logs
are added in migration 0003 (Phase 2) when their endpoints are implemented.

Revision ID: 0001
Revises: —
Create Date: 2026-05-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgvector extension — must exist before any vector column is used.
    # IF NOT EXISTS makes this safe to run multiple times.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # ------------------------------------------------------------------
    # companies
    # ------------------------------------------------------------------
    op.create_table(
        "companies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="manager"),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_users_company_id", "users", ["company_id"])
    op.create_unique_constraint("uq_users_email", "users", ["email"])

    # ------------------------------------------------------------------
    # properties
    # ------------------------------------------------------------------
    op.create_table(
        "properties",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("address", sa.String(500)),
        sa.Column("description", sa.Text),
        sa.Column("amenities", postgresql.JSONB),
        sa.Column("office_hours", postgresql.JSONB),
        sa.Column("leasing_policies", sa.Text),
        sa.Column("maintenance_instructions", sa.Text),
        sa.Column("escalation_contacts", postgresql.JSONB),
        sa.Column("business_hour_rules", postgresql.JSONB),
        sa.Column("call_handling_rules", postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_properties_company_id", "properties", ["company_id"])

    # ------------------------------------------------------------------
    # calls
    # ------------------------------------------------------------------
    op.create_table(
        "calls",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("properties.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("twilio_call_sid", sa.String(64)),
        sa.Column("livekit_room_id", sa.String(255)),
        sa.Column("caller_phone", sa.String(50)),
        sa.Column("started_at", sa.String(50)),
        sa.Column("ended_at", sa.String(50)),
        sa.Column("duration", sa.Integer),
        sa.Column("summary", sa.Text),
        sa.Column("primary_intent", sa.String(100)),
        sa.Column("sentiment", sa.String(50)),
        sa.Column("action_items", postgresql.JSONB),
        sa.Column("next_steps", sa.Text),
        sa.Column("status", sa.String(50), nullable=False, server_default="active"),
        sa.Column("escalation_status", sa.String(50), nullable=False, server_default="none"),
        sa.Column("escalation_flag", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("recording_url", sa.String(500)),
        sa.Column("lead_fields_extracted", postgresql.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_calls_property_id", "calls", ["property_id"])
    op.create_index("ix_calls_company_id", "calls", ["company_id"])
    op.create_unique_constraint("uq_calls_twilio_call_sid", "calls", ["twilio_call_sid"])
    op.create_index("ix_calls_twilio_call_sid", "calls", ["twilio_call_sid"])


def downgrade() -> None:
    op.drop_table("calls")
    op.drop_table("properties")
    op.drop_table("users")
    op.drop_table("companies")
    # Note: we intentionally do NOT drop the vector extension on downgrade —
    # another service or migration might depend on it.
