"""Add password_hash column to users table for the auth login endpoint.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-10

Upgrade SQL:
  ALTER TABLE users ADD COLUMN password_hash VARCHAR(255) NULL;
  -- Backfill the seed admin user with bcrypt.hash("demo") so that the
  -- POST /v1/auth/login endpoint works out of the box for the demo.

Downgrade SQL:
  ALTER TABLE users DROP COLUMN password_hash;
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DEMO_USER_EMAIL = "admin@sunsetapartments.example"
_DEMO_PASSWORD = "demo"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("password_hash", sa.String(255), nullable=True),
    )

    # Backfill the seed admin user with bcrypt.hash("demo") so that the demo
    # login (admin@sunsetapartments.example / "demo") works immediately after
    # running migrations. We compute the hash *at migration time* using passlib
    # so that it's always a valid hash bound to the deployment's bcrypt config.
    # Production deployments should rotate this hash via a follow-up migration
    # or admin endpoint.
    from passlib.hash import bcrypt  # noqa: PLC0415 — lazy import keeps schema-only envs unaffected

    demo_hash = bcrypt.hash(_DEMO_PASSWORD)

    op.execute(
        sa.text(
            """
            UPDATE users
               SET password_hash = :hash
             WHERE email = :email
               AND password_hash IS NULL
            """
        ).bindparams(
            sa.bindparam("hash", demo_hash),
            sa.bindparam("email", _DEMO_USER_EMAIL),
        )
    )


def downgrade() -> None:
    op.drop_column("users", "password_hash")
