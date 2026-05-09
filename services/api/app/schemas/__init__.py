# Re-export key types so route handlers can import cleanly:
#   from app.schemas import PaginatedResponse, ErrorResponse
from app.schemas.errors import ErrorDetail, ErrorResponse
from app.schemas.pagination import PaginatedResponse

__all__ = ["PaginatedResponse", "ErrorResponse", "ErrorDetail"]
