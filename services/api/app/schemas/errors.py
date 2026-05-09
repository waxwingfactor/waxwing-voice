"""Stable error envelope returned by all endpoints.

The `code` field is a contract — changing or removing codes is a breaking change.
Add new codes freely; never rename existing ones without a versioning plan.

Akhil's voice agent parses `retryable` to decide whether to retry the tool call
or surface the error to the caller. Keep this in mind when setting the field.
"""

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Stable error codes — alphabetical order, add new ones at the bottom
# ---------------------------------------------------------------------------
# fmt: off
BOOKING_SLOT_UNAVAILABLE = "BOOKING_SLOT_UNAVAILABLE"
CALL_NOT_FOUND           = "CALL_NOT_FOUND"
CALENDAR_UNAVAILABLE     = "CALENDAR_UNAVAILABLE"
DOCUMENT_NOT_FOUND       = "DOCUMENT_NOT_FOUND"
DOCUMENT_PARSE_FAILED    = "DOCUMENT_PARSE_FAILED"
EMAIL_DELIVERY_FAILED    = "EMAIL_DELIVERY_FAILED"
INVALID_REQUEST          = "INVALID_REQUEST"
LEAD_NOT_FOUND           = "LEAD_NOT_FOUND"
PROPERTY_NOT_FOUND       = "PROPERTY_NOT_FOUND"
RATE_LIMITED             = "RATE_LIMITED"
UNAUTHORIZED             = "UNAUTHORIZED"
FORBIDDEN                = "FORBIDDEN"
INTERNAL_ERROR           = "INTERNAL_ERROR"
# fmt: on


class ErrorDetail(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "BOOKING_SLOT_UNAVAILABLE",
                "message": "The selected tour slot is no longer available.",
                "retryable": False,
            }
        }
    )

    code: str
    message: str
    retryable: bool


class ErrorResponse(BaseModel):
    error: ErrorDetail
