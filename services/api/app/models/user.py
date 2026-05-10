import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    # Stored as plain string — validation enforced at Pydantic layer
    # Values: admin | manager | viewer
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="manager")
    # Values: active | inactive
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")

    # bcrypt hash produced by passlib.hash.bcrypt — never store plaintext.
    # Nullable to allow legacy / SSO-only users that have never set a password.
    # See alembic/versions/0004_add_user_password_hash.py.
    password_hash: Mapped[str | None] = mapped_column(String(255))
