"""Pydantic schemas for the auth login endpoint.

POST /v1/auth/login is intentionally unauthenticated; the response carries the
JWT used by all other endpoints. See app/api/auth.py for the handler.
"""

import uuid

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    """Body for POST /v1/auth/login.

    Validates email format via Pydantic's ``EmailStr`` (provided by the
    ``email-validator`` package, which ships with ``fastapi[standard]``).
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(..., max_length=320)
    password: str = Field(..., min_length=1, max_length=200)


class UserInfoResponse(BaseModel):
    """Public user identity returned alongside the access token."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    role: str
    company_id: uuid.UUID


class LoginResponse(BaseModel):
    """Successful login envelope. ``access_token`` is the Bearer JWT."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., description="Token lifetime in seconds.")
    user: UserInfoResponse
