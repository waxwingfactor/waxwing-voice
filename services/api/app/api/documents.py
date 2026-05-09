"""Document upload and RAG indexing endpoint.

Auth: X-Company-Id header (Phase 1 placeholder — same as all other routers).

Endpoint:
    POST /v1/documents/upload  — multipart upload; synchronous text extraction,
                                  chunking, and embedding. No background tasks.

Consumers:
    Alex (dashboard): primary consumer — operators upload property knowledge base
    documents (pet policies, floor plans, pricing sheets, FAQs) via the dashboard.
    The dashboard should poll document_id / processing_status after upload to
    confirm indexing.

Stable error codes:
    PROPERTY_NOT_FOUND     — property_id not in company scope
    UNSUPPORTED_FILE_TYPE  — extension not in {pdf, docx, txt}
    DOCUMENT_PARSE_FAILED  — text extraction raised an unexpected exception
"""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import APIError, get_company_id, get_db
from app.models.document import Document
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.property import Property
from app.rag.chunker import chunk_text
from app.rag.embedder import embed_texts
from app.rag.extractor import extract_text

router = APIRouter(prefix="/documents", tags=["documents"])

_ALLOWED_EXTENSIONS = {"pdf", "docx", "txt"}


# ---------------------------------------------------------------------------
# Response schema
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
        - Use multipart/form-data; include X-Company-Id header.
        - Poll processing_status on the returned document_id if needed.
        - In Phase 3, indexing is synchronous so the response already reflects
          "indexed" status on success.

    Example request:
        POST /v1/documents/upload
        X-Company-Id: <company_uuid>
        Content-Type: multipart/form-data

        property_id=<property_uuid>
        file=<binary file>

    Example response:
        {
            "document_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
            "file_name": "pet_policy.pdf",
            "chunk_count": 12,
            "processing_status": "indexed"
        }

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
# Helpers
# ---------------------------------------------------------------------------


async def _get_property_or_404(
    db: AsyncSession,
    property_id: uuid.UUID,
    company_id: uuid.UUID,
) -> Property:
    """Fetch property scoped to company; raise 404 PROPERTY_NOT_FOUND if absent.

    Args:
        db: Active async session.
        property_id: Target property UUID.
        company_id: Company scope from request header.

    Returns:
        ORM Property instance.

    Raises:
        APIError: 404 PROPERTY_NOT_FOUND.
    """
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


def _parse_extension(filename: str) -> str:
    """Extract the lowercase file extension (without dot) from a filename.

    Args:
        filename: Original filename string (e.g. "lease.pdf").

    Returns:
        Lowercase extension string, e.g. "pdf". Returns "" if no extension found.
    """
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()
