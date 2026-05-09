"""Pydantic schemas for Document endpoints — consumed by Alex (upload UI and knowledge view)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    property_id: uuid.UUID
    file_name: str
    file_type: str
    upload_status: str
    # Values: pending | processing | indexing | indexed | failed | retrying
    processing_status: str
    chunk_count: int
    last_indexed_at: datetime | None
    created_at: datetime


class DocumentDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    property_id: uuid.UUID
    company_id: uuid.UUID
    file_name: str
    file_type: str
    storage_key: str
    upload_status: str
    processing_status: str
    processing_attempts: int
    last_error: str | None
    chunk_count: int
    last_indexed_at: datetime | None
    uploaded_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class DocumentUploadResponse(BaseModel):
    """Returned immediately after a successful upload — before parsing starts."""

    id: uuid.UUID
    property_id: uuid.UUID
    file_name: str
    upload_status: str
    processing_status: str
    created_at: datetime


class DocumentProcessingStatusResponse(BaseModel):
    """Polling endpoint for Alex's upload progress indicator."""

    id: uuid.UUID
    processing_status: str
    processing_attempts: int
    chunk_count: int
    last_error: str | None = None
    last_indexed_at: datetime | None = None


class ReindexResponse(BaseModel):
    document_id: uuid.UUID
    processing_status: str = Field(default="pending")
