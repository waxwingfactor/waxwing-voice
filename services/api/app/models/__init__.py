# Import all models here so that Alembic autogenerate can discover every table.
# The order matters for foreign key resolution during metadata inspection.
from app.models.base import Base
from app.models.company import Company
from app.models.user import User
from app.models.property import Property
from app.models.call import Call
from app.models.lead import Lead
from app.models.booking import Booking
from app.models.email_record import EmailRecord
from app.models.document import Document
from app.models.knowledge_chunk import KnowledgeChunk
from app.models.audit_log import AuditLog

__all__ = [
    "Base",
    "Company",
    "User",
    "Property",
    "Call",
    "Lead",
    "Booking",
    "EmailRecord",
    "Document",
    "KnowledgeChunk",
    "AuditLog",
]
