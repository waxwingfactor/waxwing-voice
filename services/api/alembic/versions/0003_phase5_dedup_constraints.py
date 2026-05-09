"""Phase 5 dedup hardening — partial unique indexes for bookings and email_records.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-09

Upgrade SQL:
  CREATE UNIQUE INDEX uq_bookings_lead_slot_active
    ON bookings (lead_id, tour_date, start_time)
    WHERE status != 'cancelled';

  CREATE UNIQUE INDEX uq_email_records_call_template_recipient
    ON email_records (call_id, template_type, recipient)
    WHERE call_id IS NOT NULL;

These are partial unique indexes that cannot be expressed via
create_unique_constraint(), so op.execute() is used directly.

Downgrade SQL:
  DROP INDEX IF EXISTS uq_bookings_lead_slot_active;
  DROP INDEX IF EXISTS uq_email_records_call_template_recipient;
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Partial unique index on bookings: prevents double-booking the same lead
    # into the same slot unless one of the bookings has been cancelled.
    # ⚠ Partial index — op.execute() is required; create_unique_constraint()
    # does not support WHERE predicates.
    op.execute("""
        CREATE UNIQUE INDEX uq_bookings_lead_slot_active
        ON bookings (lead_id, tour_date, start_time)
        WHERE status != 'cancelled'
    """)

    # Partial unique index on email_records: prevents sending the same template
    # to the same recipient for the same call more than once.
    # Scoped to rows where call_id IS NOT NULL — ad-hoc emails (no call context)
    # are excluded from dedup.
    op.execute("""
        CREATE UNIQUE INDEX uq_email_records_call_template_recipient
        ON email_records (call_id, template_type, recipient)
        WHERE call_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_email_records_call_template_recipient")
    op.execute("DROP INDEX IF EXISTS uq_bookings_lead_slot_active")
