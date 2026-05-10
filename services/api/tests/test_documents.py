"""Document endpoint integration tests.

Covers:
    GET    /v1/documents/?property_id=...       — pagination + filter scope
    GET    /v1/documents/{document_id}          — happy path + 404
    DELETE /v1/documents/{document_id}          — cascade-delete chunks + audit log
    POST   /v1/documents/{document_id}/reindex  — 422 REINDEX_NOT_AVAILABLE (no S3)

Seed data must be loaded before running.
"""

import io
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.models.document import Document
from app.models.knowledge_chunk import KnowledgeChunk
from tests.conftest import COMPANY_ID, PROPERTY_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _upload_doc(
    client: AsyncClient,
    auth_headers: dict[str, str],
    *,
    property_id: uuid.UUID = PROPERTY_ID,
    body: bytes = b"Hello world. " * 50,
    filename: str = "test_doc.txt",
) -> uuid.UUID:
    """Upload a doc via POST /v1/documents/upload and return its id."""
    files = {"file": (filename, io.BytesIO(body), "text/plain")}
    data = {"property_id": str(property_id)}
    resp = await client.post(
        "/v1/documents/upload",
        files=files,
        data=data,
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return uuid.UUID(resp.json()["document_id"])


# ---------------------------------------------------------------------------
# GET /v1/documents/?property_id=...
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_documents_pagination(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """List endpoint returns the upload + correct pagination envelope."""
    doc_id = await _upload_doc(client, auth_headers)

    resp = await client.get(
        "/v1/documents/",
        params={"property_id": str(PROPERTY_ID), "page": 1, "page_size": 50},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["page"] == 1
    assert body["page_size"] == 50
    assert body["total"] >= 1
    assert isinstance(body["items"], list)
    ids = [item["id"] for item in body["items"]]
    assert str(doc_id) in ids


@pytest.mark.asyncio
async def test_list_documents_property_not_found(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """List with an unknown property_id returns 404 PROPERTY_NOT_FOUND."""
    resp = await client.get(
        "/v1/documents/",
        params={"property_id": str(uuid.uuid4())},
        headers=auth_headers,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "PROPERTY_NOT_FOUND"


@pytest.mark.asyncio
async def test_list_documents_missing_property_id_is_422(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Omitting the required property_id query param returns 422."""
    resp = await client.get("/v1/documents/", headers=auth_headers)
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# GET /v1/documents/{document_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_detail(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Detail endpoint returns the full document record for a real id."""
    doc_id = await _upload_doc(client, auth_headers, filename="detail_test.txt")

    resp = await client.get(f"/v1/documents/{doc_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == str(doc_id)
    assert data["file_name"] == "detail_test.txt"
    assert data["file_type"] == "txt"
    assert data["company_id"] == str(COMPANY_ID)


@pytest.mark.asyncio
async def test_get_document_not_found(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Detail with an unknown id returns 404 DOCUMENT_NOT_FOUND."""
    resp = await client.get(f"/v1/documents/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


# ---------------------------------------------------------------------------
# DELETE /v1/documents/{document_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_document_cascades_chunks_and_writes_audit_log(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE removes the doc + its chunks; writes a DOCUMENT_DELETED audit log."""
    # Use a multi-paragraph body so chunker produces at least one chunk.
    body = (b"Pet policy: cats and dogs welcome.\n" * 100) + b"End of doc."
    doc_id = await _upload_doc(
        client, auth_headers, body=body, filename="delete_cascade.txt"
    )

    # Verify chunks exist before deletion.
    async with AsyncSessionLocal() as session:
        before = await session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == doc_id)
        )
        assert (before or 0) >= 1, "Expected at least one chunk before delete"

    resp = await client.delete(f"/v1/documents/{doc_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body_resp = resp.json()
    assert body_resp["deleted"] is True
    assert body_resp["document_id"] == str(doc_id)
    assert body_resp["chunks_deleted"] >= 1

    # Verify cascade and audit log via a fresh session.
    async with AsyncSessionLocal() as session:
        # Doc is gone
        doc_row = await session.scalar(
            select(Document).where(Document.id == doc_id)
        )
        assert doc_row is None

        # All chunks for this document are gone
        chunk_count = await session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == doc_id)
        )
        assert (chunk_count or 0) == 0

        # Audit log written
        audit = await session.scalar(
            select(AuditLog)
            .where(
                AuditLog.action == "DOCUMENT_DELETED",
                AuditLog.entity_id == str(doc_id),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
        assert audit is not None
        assert audit.entity_type == "document"
        assert audit.metadata_ is not None
        assert audit.metadata_.get("file_name") == "delete_cascade.txt"
        assert "chunk_count_at_delete" in audit.metadata_


@pytest.mark.asyncio
async def test_delete_document_not_found(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """DELETE on an unknown id returns 404 DOCUMENT_NOT_FOUND (idempotent)."""
    resp = await client.delete(f"/v1/documents/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"


# ---------------------------------------------------------------------------
# POST /v1/documents/{document_id}/reindex
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reindex_returns_422_when_s3_not_configured(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Reindex returns REINDEX_NOT_AVAILABLE because no S3 client exists today."""
    doc_id = await _upload_doc(client, auth_headers, filename="reindex_test.txt")

    resp = await client.post(
        f"/v1/documents/{doc_id}/reindex",
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "REINDEX_NOT_AVAILABLE"


@pytest.mark.asyncio
async def test_reindex_unknown_document_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Reindex on unknown id returns 404 (scope check runs before S3 check)."""
    resp = await client.post(
        f"/v1/documents/{uuid.uuid4()}/reindex",
        headers=auth_headers,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"
