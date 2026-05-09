# Import all models here so that Alembic autogenerate can discover every table.
# The order matters for foreign key resolution during metadata inspection.
from app.models.audit_log import AuditLog
from app.models.base import Base
from app.models.booking import Booking
from app.models.call import Call
from app.models.call_event import CallEvent
from app.models.company import Company
from app.models.document import Document
from app.models.email_record import EmailRecord
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.lead import Lead
from app.models.property import Property
from app.models.transcript_segment import TranscriptSegment
from app.models.user import User

__all__ = [
    "Base",
    "Company",
    "User",
    "Property",
    "Call",
    "TranscriptSegment",
    "CallEvent",
    "Lead",
    "Booking",
    "EmailRecord",
    "Document",
    "KnowledgeChunk",
    "AuditLog",
]
