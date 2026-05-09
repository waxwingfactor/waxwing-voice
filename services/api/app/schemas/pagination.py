"""Generic pagination envelope used by all list endpoints.

Usage in a route handler:
    items = await db.execute(select(Call).where(...).offset(offset).limit(page_size))
    total = await db.scalar(select(func.count()).where(...))
    return PaginatedResponse[CallListItem](
        items=[CallListItem.model_validate(c) for c in items.scalars()],
        total=total,
        page=page,
        page_size=page_size,
    )
"""

from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(ge=0, description="Total matching records across all pages")
    page: int = Field(ge=1, description="Current page number (1-indexed)")
    page_size: int = Field(ge=1, le=100, description="Records per page (max 100)")
