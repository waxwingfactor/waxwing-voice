"""Document upload, listing, retrieval, deletion, and re-indexing endpoints.

Auth: Bearer JWT (see app.database.get_company_id).

Endpoints:
    POST   /v1/documents/upload                 -> multipart upload + sync indexing
    GET    /v1/documents/?property_id=...       -> paginated list filtered by property
    GET    /v1/documents/{document_id}          -> full document detail
    DELETE /v1/documents/{document_id}          -> delete document + cascade chunks
    POST   /v1/documents/{document_id}/reindex  -> re-run extract+chunk+embed pipeline

Consumers:
    Alex (dashboard): primary consumer — operators upload property knowledge base
    documents (pet policies, floor plans, pricing sheets, FAQs) via the dashboard.
    The dashboard polls processing_status after upload to confirm indexing.

Stable error codes:
    PROPERTY_NOT_FOUND      — property_id not in company scope
    DOCUMENT_NOT_FOUND      — document_id not in company scope
    UNSUPPORTED_FILE_TYPE   — extension not in {pdf, docx, txt}
    DOCUMENT_PARSE_FAILED   — text extraction raised an unexpected exception
    REINDEX_NOT_AVAILABLE   — original file is unavailable (S3 not configured),
                              so the document cannot be re-indexed.
"""

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import APIError, get_company_id, get_db
from app.limiter import limiter
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.property import Property
from app.rag.chunker import chunk_text
from app.rag.embedder import embed_texts
from app.rag.extractor import extract_text
from app.schemas.documents import (
    DocumentDetailResponse,
    DocumentListItem,
    ReindexResponse,
)
from app.schemas.pagination import PaginatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

_ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}


# ---------------------------------------------------------------------------
# Response schema for the upload endpoint (kept here for backward compat —
# DocumentUploadResponse exists in schemas/documents.py but with a different
# shape; the upload endpoint pre-dates that file).
# ---------------------------------------------------------------------------


class DocumentUploadResponse(BaseModel):
    """Returned after a successful synchronous document upload and indexing."""

    document_id: str
    file_name: str
    chunk_count: int
    processing_status: str


# ---------------------------------------------------------------------------
# POST /documents/upload
# ---------------------------------------------------------------------------


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    property_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> DocumentUploadResponse:
    """Upload a document and synchronously index it into the RAG knowledge base.

    Accepts multipart/form-data with a property_id field and a file upload.
    Text is extracted, chunked (~1800 chars/chunk), embedded via OpenAI
    text-embedding-ada-002, and stored in the knowledge_chunks table scoped to
    the property.

    If OPENAI_API_KEY is not configured, zero vectors are stored — retrieval
    will return no results until a real key is set, but the document row and
    chunks are persisted.

    Consumer Notes (Alex — dashboard):
        - Use multipart/form-data; include Authorization: Bearer <jwt> header.
        - Poll processing_status on the returned document_id if needed.
        - In Phase 3, indexing is synchronous so the response already reflects
          "indexed" status on success.

    Errors:
        404 PROPERTY_NOT_FOUND     — property_id not in company scope
        400 UNSUPPORTED_FILE_TYPE  — file extension not in {pdf, docx, txt}
        422 DOCUMENT_PARSE_FAILED  — text extraction failed unexpectedly
    """
    # 1. Validate company owns the property.
    await _get_property_or_404(db, property_id, company_id)

    # 2. Validate file extension.
    filename = file.filename or ""
    file_type = _parse_extension(filename)
    if file_type not in _ALLOWED_EXTENSIONS:
        raise APIError(
            status_code=400,
            code="UNSUPPORTED_FILE_TYPE",
            message="Only pdf, docx, and txt files are supported.",
        )

    # 3. Read file bytes.
    file_bytes = await file.read()

    # 4. Extract text.
    try:
        text = extract_text(file_bytes, file_type)
    except Exception as exc:
        raise APIError(
            status_code=422,
            code="DOCUMENT_PARSE_FAILED",
            message=f"Failed to extract text from the uploaded file: {type(exc).__name__}.",
        ) from exc

    # 5. Chunk text.
    chunks = chunk_text(text, source_label=filename)

    # 6. Create Document row and flush to get an id.
    doc_id = uuid.uuid4()
    doc = Document(
        id=doc_id,
        property_id=property_id,
        company_id=company_id,
        uploaded_by=None,
        file_name=filename,
        file_type=file_type,
        storage_key=f"{property_id}/{doc_id}/{filename}",
        upload_status="uploaded",
        processing_status="indexing",
        chunk_count=0,
    )
    db.add(doc)
    await db.flush()  # populates doc.id

    # 7. Embed chunks (falls back to zero vectors if no api_key configured).
    from app.config import get_settings  # imported here to avoid circular import

    settings = get_settings()
    chunk_texts_list = [c["chunk_text"] for c in chunks]
    vectors = await embed_texts(chunk_texts_list, settings.openai_api_key)

    # 8. Insert KnowledgeChunk rows.
    knowledge_chunks = [
        KnowledgeChunk(
            document_id=doc.id,
            property_id=property_id,
            company_id=company_id,
            chunk_text=chunk["chunk_text"],
            source_label=chunk["source_label"],
            page_number=chunk["page_number"],
            embedding=vector,
        )
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]
    db.add_all(knowledge_chunks)

    # 9. Mark document as indexed.
    doc.processing_status = "indexed"
    doc.chunk_count = len(chunks)
    doc.last_indexed_at = datetime.now(UTC)

    # 10. Commit (get_db dependency handles rollback on error).
    await db.commit()

    return DocumentUploadResponse(
        document_id=str(doc.id),
        file_name=filename,
        chunk_count=len(chunks),
        processing_status="indexed",
    )


# ---------------------------------------------------------------------------
# GET /documents/?property_id=...
# ---------------------------------------------------------------------------


@router.get("/", response_model=PaginatedResponse[DocumentListItem])
@limiter.limit("60/minute")
async def list_documents(
    request: Request,
    property_id: uuid.UUID = Query(..., description="Filter by property (required)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> PaginatedResponse[DocumentListItem]:
    """Return a paginated list of documents for a property, newest-first.

    Consumer Notes (Alex — dashboard):
        - ``property_id`` is required; cross-property lists are not supported.
        - Results are ordered by ``created_at DESC``.
        - ``processing_status`` cycles through pending → processing →
          indexing → indexed (or failed / retrying on error).

    Errors:
        404 PROPERTY_NOT_FOUND — property_id not in company scope.

    Example:
        GET /v1/documents/?property_id=...&page=1&page_size=20
    """
    await _get_property_or_404(db, property_id, company_id)

    base_where = (
        Document.property_id == property_id,
        Document.company_id == company_id,
    )

    total: int = (
        await db.scalar(select(func.count()).select_from(Document).where(*base_where)) or 0
    )

    result = await db.execute(
        select(Document)
        .where(*base_where)
        .order_by(Document.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    documents = result.scalars().all()

    return PaginatedResponse[DocumentListItem](
        items=[DocumentListItem.model_validate(d) for d in documents],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /documents/{document_id}
# ---------------------------------------------------------------------------


@router.get("/{document_id}", response_model=DocumentDetailResponse)
@limiter.limit("60/minute")
async def get_document(
    request: Request,
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> DocumentDetailResponse:
    """Return the full document record.

    Errors:
        404 DOCUMENT_NOT_FOUND — document not in company scope.
    """
    doc = await _get_document_or_404(db, document_id, company_id)
    return DocumentDetailResponse.model_validate(doc)


# ---------------------------------------------------------------------------
# DELETE /documents/{document_id}
# ---------------------------------------------------------------------------


class DocumentDeleteResponse(BaseModel):
    """Body returned by DELETE /v1/documents/{id}."""

    deleted: bool = True
    document_id: uuid.UUID
    chunks_deleted: int


@router.delete("/{document_id}", response_model=DocumentDeleteResponse)
@limiter.limit("30/minute")
async def delete_document(
    request: Request,
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> DocumentDeleteResponse:
    """Delete a document and its associated knowledge chunks.

    The ``knowledge_chunks.document_id`` column has no ForeignKey constraint
    (per migration 0002 — chunks are intentionally allowed to outlive the
    parent document for reindex idempotency). This endpoint therefore
    explicitly deletes chunks scoped to (document_id, company_id).

    Audit:
        Writes a ``DOCUMENT_DELETED`` row with metadata
        ``{"file_name": ..., "chunk_count_at_delete": ...}``.

    Errors:
        404 DOCUMENT_NOT_FOUND — document not in company scope (also returned
        if the document was already deleted; this is idempotent).
    """
    doc = await _get_document_or_404(db, document_id, company_id)

    file_name = doc.file_name
    property_id = doc.property_id

    # Count chunks first so we can record the count in the audit log.
    chunks_count: int = (
        await db.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(
                KnowledgeChunk.document_id == document_id,
                KnowledgeChunk.company_id == company_id,
            )
        )
        or 0
    )

    # Cascade-delete chunks (no FK constraint exists; explicit delete required).
    await db.execute(
        delete(KnowledgeChunk).where(
            KnowledgeChunk.document_id == document_id,
            KnowledgeChunk.company_id == company_id,
        )
    )

    await db.delete(doc)
    await db.flush()

    audit = AuditLog(
        company_id=company_id,
        property_id=property_id,
        actor_type="USER",
        actor_id=str(company_id),
        action="DOCUMENT_DELETED",
        entity_type="document",
        entity_id=str(document_id),
        metadata_={"file_name": file_name, "chunk_count_at_delete": chunks_count},
    )
    db.add(audit)
    await db.flush()

    logger.info(
        "Document %s deleted by company %s; chunks_deleted=%d",
        document_id,
        company_id,
        chunks_count,
    )

    return DocumentDeleteResponse(
        deleted=True,
        document_id=document_id,
        chunks_deleted=chunks_count,
    )


# ---------------------------------------------------------------------------
# POST /documents/{document_id}/reindex
# ---------------------------------------------------------------------------


@router.post("/{document_id}/reindex", response_model=ReindexResponse)
@limiter.limit("30/minute")
async def reindex_document(
    request: Request,
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> ReindexResponse:
    """Re-run the chunk + embed pipeline against the original uploaded file.

    Currently the upload endpoint records ``storage_key`` on the Document row
    but does NOT push file bytes to S3 (no S3 client is configured anywhere in
    this codebase — see app/config.py). Without S3 we cannot recover the
    original bytes, so reindex returns ``422 REINDEX_NOT_AVAILABLE``.

    Once S3 is wired up (Phase 6+), the body of this endpoint should:
      1. Fetch ``doc.storage_key`` from S3 → bytes.
      2. ``extract_text`` + ``chunk_text`` + ``embed_texts``.
      3. Delete the document's existing knowledge_chunks.
      4. Insert the new chunks; update ``doc.chunk_count``,
         ``doc.last_indexed_at``, ``doc.processing_status = "indexed"``.
      5. Write a ``DOCUMENT_REINDEXED`` audit log with
         ``{"old_chunk_count": ..., "new_chunk_count": ...}``.

    Errors:
        404 DOCUMENT_NOT_FOUND     — document not in company scope.
        422 REINDEX_NOT_AVAILABLE  — S3 storage is not configured, original
                                     file bytes are unrecoverable.
    """
    doc = await _get_document_or_404(db, document_id, company_id)

    from app.config import get_settings

    settings = get_settings()

    # Sanity check for any future S3 configuration. Today no s3_* settings exist
    # so this is always falsy; the endpoint always returns REINDEX_NOT_AVAILABLE.
    s3_configured = bool(getattr(settings, "s3_bucket", "")) and bool(doc.storage_key)

    if not s3_configured:
        logger.info(
            "Reindex requested for document %s by company %s but S3 is not configured.",
            document_id,
            company_id,
        )
        raise APIError(
            status_code=422,
            code="REINDEX_NOT_AVAILABLE",
            message=(
                "Re-indexing is not available because object storage (S3) is not "
                "configured. The original file bytes cannot be re-fetched. Re-upload "
                "the document to refresh its knowledge chunks."
            ),
        )

    # ---- Future implementation (S3-backed) — kept inline for clarity ----
    # old_chunk_count = doc.chunk_count or 0
    # file_bytes = await fetch_from_s3(doc.storage_key)
    # text = extract_text(file_bytes, doc.file_type)
    # chunks = chunk_text(text, source_label=doc.file_name)
    # await db.execute(delete(KnowledgeChunk).where(
    #     KnowledgeChunk.document_id == document_id,
    #     KnowledgeChunk.company_id == company_id,
    # ))
    # vectors = await embed_texts([c["chunk_text"] for c in chunks], settings.openai_api_key)
    # db.add_all([
    #     KnowledgeChunk(
    #         document_id=document_id,
    #         property_id=doc.property_id,
    #         company_id=company_id,
    #         chunk_text=c["chunk_text"],
    #         source_label=c["source_label"],
    #         page_number=c["page_number"],
    #         embedding=v,
    #     )
    #     for c, v in zip(chunks, vectors, strict=True)
    # ])
    # doc.chunk_count = len(chunks)
    # doc.processing_status = "indexed"
    # doc.last_indexed_at = datetime.now(UTC)
    # db.add(AuditLog(..., action="DOCUMENT_REINDEXED",
    #     metadata_={"old_chunk_count": old_chunk_count, "new_chunk_count": len(chunks)}))
    # await db.flush()
    # return ReindexResponse(document_id=document_id, processing_status="indexed")

    # Unreachable today — kept here so type-checkers see a return path.
    return ReindexResponse(document_id=document_id, processing_status="indexed")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_property_or_404(
    db: AsyncSession,
    property_id: uuid.UUID,
    company_id: uuid.UUID,
) -> Property:
    """Fetch property scoped to company; raise 404 PROPERTY_NOT_FOUND if absent."""
    result = await db.execute(
        select(Property).where(
            Property.id == property_id,
            Property.company_id == company_id,
        )
    )
    prop = result.scalar_one_or_none()
    if prop is None:
        raise APIError(
            status_code=404,
            code="PROPERTY_NOT_FOUND",
            message=(
                "Property not found or does not belong to this company. "
                "Verify the property_id is correct for this account."
            ),
        )
    return prop


async def _get_document_or_404(
    db: AsyncSession,
    document_id: uuid.UUID,
    company_id: uuid.UUID,
) -> Document:
    """Fetch document scoped to company; raise 404 DOCUMENT_NOT_FOUND if absent."""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.company_id == company_id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise APIError(
            status_code=404,
            code="DOCUMENT_NOT_FOUND",
            message="Document not found or does not belong to this company.",
        )
    return doc


def _parse_extension(filename: str) -> str:
    """Extract the lowercase file extension (without dot) from a filename."""
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()
