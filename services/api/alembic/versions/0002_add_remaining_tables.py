"""Add all remaining tables: transcript_segments, call_events, leads, bookings,
email_records, documents, knowledge_chunks, audit_logs.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-09

Upgrade SQL (summary):
  CREATE TABLE transcript_segments (...)
  CREATE TABLE call_events (...)
  CREATE TABLE leads (...)
  CREATE TABLE bookings (...)
  CREATE TABLE email_records (...)
  CREATE TABLE documents (...)
  CREATE TABLE knowledge_chunks (...)
  CREATE TABLE audit_logs (...)

Downgrade SQL (summary — reverse dependency order):
  DROP TABLE audit_logs
  DROP TABLE knowledge_chunks
  DROP TABLE documents
  DROP TABLE email_records
  DROP TABLE bookings
  DROP TABLE leads
  DROP TABLE call_events
  DROP TABLE transcript_segments
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # transcript_segments — append-only, one row per speaker turn
    # ------------------------------------------------------------------
    op.create_table(
        "transcript_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "call_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("speaker", sa.String(20), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("timestamp", sa.Float, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_transcript_segments_call_id", "transcript_segments", ["call_id"])

    # ------------------------------------------------------------------
    # call_events — append-only structured events from the voice agent
    # ------------------------------------------------------------------
    op.create_table(
        "call_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "call_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calls.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("occurred_at", sa.String(50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_call_events_call_id", "call_events", ["call_id"])
    op.create_index("ix_call_events_event_type", "call_events", ["event_type"])

    # ------------------------------------------------------------------
    # leads — upsert key: (property_id, phone)
    # ------------------------------------------------------------------
    op.create_table(
        "leads",
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
        sa.Column(
            "call_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calls.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("budget", sa.Numeric(10, 2), nullable=True),
        sa.Column("move_in_date", sa.Date, nullable=True),
        sa.Column("desired_unit_type", sa.String(100), nullable=True),
        sa.Column("pet_info", postgresql.JSONB, nullable=True),
        sa.Column("number_of_occupants", sa.Integer, nullable=True),
        sa.Column("reason_for_moving", sa.Text, nullable=True),
        sa.Column("how_heard", sa.String(255), nullable=True),
        sa.Column("urgency", sa.String(50), nullable=True),
        sa.Column("tour_interest", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("lead_score", sa.String(20), nullable=True),
        sa.Column(
            "lead_status",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'new'"),
        ),
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
    op.create_index("ix_leads_property_id", "leads", ["property_id"])
    op.create_index("ix_leads_company_id", "leads", ["company_id"])
    op.create_index("ix_leads_call_id", "leads", ["call_id"])
    op.create_index("ix_leads_phone", "leads", ["phone"])
    # ⚠️ Unique constraint used by the voice tool upsert (INSERT ... ON CONFLICT)
    op.create_unique_constraint("uq_leads_property_phone", "leads", ["property_id", "phone"])

    # ------------------------------------------------------------------
    # bookings
    # ------------------------------------------------------------------
    op.create_table(
        "bookings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
        sa.Column(
            "call_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calls.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "calendar_provider",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'google_calendar'"),
        ),
        sa.Column("calendar_event_id", sa.String(255), nullable=True),
        sa.Column("tour_date", sa.Date, nullable=True),
        sa.Column("start_time", sa.Time, nullable=True),
        sa.Column("end_time", sa.Time, nullable=True),
        sa.Column("tour_type", sa.String(50), nullable=True),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'confirmed'"),
        ),
        sa.Column(
            "confirmation_email_status",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
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
    op.create_index("ix_bookings_lead_id", "bookings", ["lead_id"])
    op.create_index("ix_bookings_property_id", "bookings", ["property_id"])
    op.create_index("ix_bookings_company_id", "bookings", ["company_id"])

    # ------------------------------------------------------------------
    # email_records
    # ------------------------------------------------------------------
    op.create_table(
        "email_records",
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
        sa.Column(
            "call_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("calls.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "lead_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("leads.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("recipient", sa.String(320), nullable=False),
        sa.Column("subject", sa.String(998), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("template_type", sa.String(50), nullable=True),
        sa.Column("delivery_provider", sa.String(50), nullable=True),
        sa.Column(
            "delivery_status",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_email_records_property_id", "email_records", ["property_id"])
    op.create_index("ix_email_records_company_id", "email_records", ["company_id"])

    # ------------------------------------------------------------------
    # documents
    # ------------------------------------------------------------------
    op.create_table(
        "documents",
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
        sa.Column(
            "uploaded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("file_name", sa.String(500), nullable=False),
        sa.Column("file_type", sa.String(20), nullable=False),
        sa.Column("storage_key", sa.String(1000), nullable=True),
        sa.Column("extracted_text_ref", sa.String(1000), nullable=True),
        sa.Column(
            "upload_status",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "processing_status",
            sa.String(50),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "processing_attempts",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("chunk_count", sa.Integer, nullable=True),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_documents_property_id", "documents", ["property_id"])
    op.create_index("ix_documents_company_id", "documents", ["company_id"])

    # ------------------------------------------------------------------
    # knowledge_chunks — pgvector embeddings, always filtered by property_id
    # ⚠️ document_id has NO FK — documents may be deleted without losing chunks
    # ⚠️ Retrieval queries MUST include WHERE property_id = :property_id
    # ------------------------------------------------------------------
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # No FK to documents — intentional; chunks survive document deletion
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.Column("chunk_text", sa.Text, nullable=False),
        sa.Column("source_label", sa.String(500), nullable=True),
        sa.Column("page_number", sa.Integer, nullable=True),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_knowledge_chunks_property_id", "knowledge_chunks", ["property_id"])
    op.create_index("ix_knowledge_chunks_company_id", "knowledge_chunks", ["company_id"])

    # ------------------------------------------------------------------
    # audit_logs — append-only compliance trail, no PII in metadata
    # ------------------------------------------------------------------
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "property_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("properties.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_type", sa.String(50), nullable=False),
        sa.Column("actor_id", sa.String(255), nullable=False),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(255), nullable=False),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_audit_logs_company_id", "audit_logs", ["company_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])


def downgrade() -> None:
    # Drop in reverse dependency order
    op.drop_table("audit_logs")
    op.drop_table("knowledge_chunks")
    op.drop_table("documents")
    op.drop_table("email_records")
    op.drop_table("bookings")
    op.drop_table("leads")
    op.drop_table("call_events")
    op.drop_table("transcript_segments")
