"""Model registry.

Importing this package registers every model on the shared declarative
``Base.metadata`` so Alembic autogenerate and ``create_all`` see the full
schema.
"""

from . import audit, job, knowledge, learning, org, portfolio, retention, user
from .audit import AuditEvent
from .base import GUID, Base, IdMixin, TimestampMixin
from .job import Job, JobStatus
from .knowledge import (
    Claim,
    ClaimType,
    Concept,
    ConceptRelationship,
    ConceptVersion,
    GraphBuild,
    GraphBuildStatus,
    GraphVersion,
    LearningObjective,
    LicenseStatus,
    RelationshipType,
    Source,
    SourceDocument,
    SourcePassage,
    SourceType,
    Topic,
    VerificationStatus,
)
from .learning import (
    AssessmentAttempt,
    CriterionResult,
    Diagnosis,
    MasteryEstimate,
    MasteryState,
    Question,
    Rubric,
)
from .org import Org, OrgMembership, Role
from .portfolio import PortfolioItem, PortfolioSetting
from .retention import RetentionSchedule, RetentionStatus, ReviewAttempt
from .user import RefreshToken, User

__all__ = [
    "Base",
    "GUID",
    "IdMixin",
    "TimestampMixin",
    "User",
    "RefreshToken",
    "Org",
    "OrgMembership",
    "Role",
    "AuditEvent",
    "Source",
    "SourceType",
    "LicenseStatus",
    "VerificationStatus",
    "SourceDocument",
    "SourcePassage",
    "Topic",
    "Concept",
    "ConceptVersion",
    "ConceptRelationship",
    "RelationshipType",
    "Claim",
    "ClaimType",
    "GraphBuild",
    "GraphBuildStatus",
    "GraphVersion",
    "LearningObjective",
    "Question",
    "Rubric",
    "AssessmentAttempt",
    "CriterionResult",
    "MasteryEstimate",
    "MasteryState",
    "Diagnosis",
    "RetentionSchedule",
    "RetentionStatus",
    "ReviewAttempt",
    "PortfolioSetting",
    "PortfolioItem",
    "Job",
    "JobStatus",
    "audit",
    "job",
    "knowledge",
    "learning",
    "org",
    "portfolio",
    "retention",
    "user",
]
