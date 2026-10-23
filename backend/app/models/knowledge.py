"""Knowledge-domain models: sources, documents, topics, concepts, graphs.

These models implement the evidence/provenance model required by the product:

  * Sources track licensing and permitted-use status explicitly.
  * Documents keep the raw content hash and extracted text.
  * Passages are the smallest citable unit.
  * Claims carry a provenance type so synthesized or inferred statements are
    never presented as source-grounded facts.
  * Graph versions are immutable snapshots; publication creates a new version.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from ..db import Base
from .base import GUID, IdMixin, TimestampMixin, UTCDateTime, utcnow
from .documents import SegmentationStatus


class SourceType(str, Enum):
    documentation = "documentation"
    standard = "standard"
    academic_metadata = "academic_metadata"
    academic_paper = "academic_paper"
    course_material = "course_material"
    textbook = "textbook"
    user_upload = "user_upload"
    web = "web"


class LicenseStatus(str, Enum):
    permitted = "permitted"
    permitted_with_conditions = "permitted_with_conditions"
    unknown = "unknown"
    restricted = "restricted"
    not_permitted = "not_permitted"


class VerificationStatus(str, Enum):
    unverified = "unverified"
    pending = "pending"
    verified = "verified"
    contradicted = "contradicted"
    unsupported = "unsupported"


class RelationshipType(str, Enum):
    prerequisite_of = "prerequisite_of"
    part_of = "part_of"
    related_to = "related_to"
    implements = "implements"
    extends = "extends"
    contrasts_with = "contrasts_with"
    example_of = "example_of"
    applies_to = "applies_to"


class ClaimType(str, Enum):
    source_grounded = "source_grounded"
    synthesized = "synthesized"
    inference = "inference"
    unverified = "unverified"


class GraphBuildStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class Source(IdMixin, TimestampMixin, Base):
    __tablename__ = "sources"

    org_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    canonical_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    publisher: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    license_status: Mapped[str] = mapped_column(
        String(40), default=LicenseStatus.unknown.value, nullable=False
    )
    license_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    access_method: Mapped[str] = mapped_column(String(60), default="api", nullable=False)
    terms_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retrieved_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    extraction_method: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)
    extraction_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(40), default=VerificationStatus.unverified.value, nullable=False
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)


class SourceDocument(IdMixin, TimestampMixin, Base):
    """A stored document (fetched from a source, or uploaded by a user).

    Uploaded documents additionally carry an owner, a tenant, and a processing
    state that drives the OCR pipeline. Fetched documents keep ``NULL`` in the
    processing columns: they are managed by ``status`` and the ingestion path,
    so they are never mislabelled as awaiting OCR.

    Coordinate/geometry data for recognized content lives in ``DocumentPage``
    and ``OcrRegion``; this row only tracks the original artifact.
    """

    __tablename__ = "source_documents"

    source_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("sources.id", ondelete="CASCADE"), index=True, nullable=False
    )
    topic_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=True
    )
    # --- ownership / tenancy (uploads) ------------------------------------
    owner_user_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True
    )
    org_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    # Untrusted display metadata only; never used to build storage paths.
    original_filename: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True
    )
    # --- document-processing lifecycle (uploads) --------------------------
    # NULL means "not part of the OCR pipeline" (fetched documents).
    processing_state: Mapped[Optional[str]] = mapped_column(
        String(30), index=True, nullable=True
    )
    processing_version: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True
    )
    # Bounded, safe error category + description; never internal exception text.
    error_category: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    error_detail: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_job_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("jobs.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # --- segmentation lifecycle (Checkpoint E) ----------------------------
    # Tracked independently of ``processing_state`` so segmentation can fail or
    # be re-run without mutating the OCR pipeline's state machine. ``not_run``
    # is the default: a prepared/recognized document is not automatically
    # segmented, and absence of segments never implies failure.
    segmentation_status: Mapped[str] = mapped_column(
        String(30),
        default=SegmentationStatus.not_run.value,
        nullable=False,
        index=True,
    )
    # Algorithm version of the last completed segmentation (reprocessing /
    # version-change provenance; see ``DocumentSegment.algorithm_version``).
    segmentation_version: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True
    )
    # Bounded, safe segmentation failure category + detail; never exception text.
    segmentation_error_category: Mapped[Optional[str]] = mapped_column(
        String(40), nullable=True
    )
    segmentation_error_detail: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )
    last_segment_job_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("jobs.id", ondelete="SET NULL"), index=True, nullable=True
    )
    # Monotonic run counter; gives each reprocessing request a distinct job
    # idempotency key so a second run is a new job while a concurrent first
    # request still de-duplicates.
    segmentation_run: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # --- original content -------------------------------------------------
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    storage_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="pending", nullable=False)


class SourcePassage(IdMixin, TimestampMixin, Base):
    __tablename__ = "source_passages"

    document_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("source_documents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    locator: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)


class Topic(IdMixin, TimestampMixin, Base):
    __tablename__ = "topics"

    org_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    slug: Mapped[str] = mapped_column(String(300), index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_public: Mapped[bool] = mapped_column(default=False, nullable=False)

    __table_args__ = (UniqueConstraint("org_id", "slug", name="uq_topic_org_slug"),)


class Concept(IdMixin, TimestampMixin, Base):
    __tablename__ = "concepts"

    topic_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=False
    )
    stable_key: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(300), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    definition: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    explanation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    misconceptions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    difficulty: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    learning_objectives: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(40), default=VerificationStatus.unverified.value, nullable=False
    )
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True
    )

    __table_args__ = (UniqueConstraint("topic_id", "stable_key", name="uq_concept_topic_key"),)


class ConceptVersion(IdMixin, Base):
    """Immutable snapshots of a concept's mutable fields."""

    __tablename__ = "concept_versions"

    concept_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("concepts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )

    __table_args__ = (UniqueConstraint("concept_id", "version", name="uq_concept_version"),)


class ConceptRelationship(IdMixin, TimestampMixin, Base):
    __tablename__ = "concept_relationships"

    from_concept_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("concepts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    to_concept_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("concepts.id", ondelete="CASCADE"), index=True, nullable=False
    )
    relationship_type: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), default=VerificationStatus.unverified.value, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "from_concept_id",
            "to_concept_id",
            "relationship_type",
            name="uq_relationship",
        ),
        CheckConstraint("from_concept_id <> to_concept_id", name="ck_no_self_relationship"),
    )


class Claim(IdMixin, TimestampMixin, Base):
    """A single citable statement tied to a source passage."""

    __tablename__ = "claims"

    concept_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("concepts.id", ondelete="CASCADE"), index=True, nullable=True
    )
    document_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("source_documents.id", ondelete="CASCADE"), index=True, nullable=True
    )
    passage_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("source_passages.id", ondelete="CASCADE"), index=True, nullable=True
    )
    claim_type: Mapped[str] = mapped_column(String(40), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    source_span: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), default=VerificationStatus.unverified.value, nullable=False
    )


class GraphBuild(IdMixin, TimestampMixin, Base):
    __tablename__ = "graph_builds"

    topic_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(30), default=GraphBuildStatus.pending.value, nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(40), default="manual", nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class GraphVersion(IdMixin, Base):
    """Immutable published graph snapshot. Updates always create a new row."""

    __tablename__ = "graph_versions"

    build_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("graph_builds.id", ondelete="CASCADE"), index=True, nullable=False
    )
    topic_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="published", nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    validation_report: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    concept_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    relationship_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )

    __table_args__ = (UniqueConstraint("topic_id", "version", name="uq_graph_version"),)


class LearningObjective(IdMixin, TimestampMixin, Base):
    __tablename__ = "learning_objectives"

    topic_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("topics.id", ondelete="CASCADE"), index=True, nullable=False
    )
    concept_id: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("concepts.id", ondelete="CASCADE"), index=True, nullable=True
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
