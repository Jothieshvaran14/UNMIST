"""End-to-end graph build, publication, and rollback tests."""

from __future__ import annotations

import hashlib

import pytest
from app.errors import ValidationError
from app.models.knowledge import (
    Concept,
    ConceptRelationship,
    Source,
    SourceDocument,
    SourcePassage,
    Topic,
    VerificationStatus,
)
from app.services.knowledge import graph_service
from app.services.knowledge.concept_extraction import (
    DeterministicExtractor,
    ExtractionResult,
)
from app.services.knowledge.validation import ConceptDraft, RelationshipDraft

PASSAGES = [
    "Decorators are functions that modify other functions.",
    "First-class functions are functions that can be passed as arguments and returned as values.",
    "Closures are a prerequisite for decorators.",
]


def _seed_document(session, topic: Topic, passages: list[str]) -> SourceDocument:
    source = Source(
        org_id=topic.org_id,
        source_type="documentation",
        canonical_url="https://example.com/decorators",
        license_status="permitted",
        publisher="Example Org",
        title="Decorators",
        extraction_method="deterministic-v1",
        extraction_version="1",
    )
    session.add(source)
    session.flush()
    document = SourceDocument(
        source_id=source.id,
        topic_id=topic.id,
        title="Decorators",
        url="https://example.com/decorators",
        media_type="text/html",
        content_hash="abc123",
        size_bytes=100,
        extracted_text=" ".join(passages),
        status="extracted",
    )
    session.add(document)
    session.flush()
    for index, passage in enumerate(passages):
        session.add(
            SourcePassage(
                document_id=document.id,
                locator=f"passage:{index}",
                text=passage,
                content_hash=hashlib.sha256(passage.encode()).hexdigest(),
            )
        )
    session.flush()
    return document


def test_build_publishes_versioned_graph(db_session):
    topic = graph_service.create_topic(
        db_session, actor_id=None, org_id=None, name="Python Decorators"
    )
    _seed_document(db_session, topic, PASSAGES)

    build = graph_service.run_graph_build(
        db_session, topic=topic, extractor=DeterministicExtractor(), created_by=None
    )
    assert build.status == "succeeded", build.error

    versions = graph_service.list_graph_versions(db_session, topic.id)
    assert len(versions) == 1
    version = versions[0]
    assert version.concept_count >= 3
    assert version.relationship_count >= 1
    assert version.validation_report["ok"] is True

    concepts = db_session.query(Concept).filter(Concept.topic_id == topic.id).all()
    assert len(concepts) >= 3
    # Every persisted concept must be unverified until independently reviewed.
    assert all(c.verification_status == VerificationStatus.unverified.value for c in concepts)

    relationships = (
        db_session.query(ConceptRelationship)
        .join(Concept, Concept.id == ConceptRelationship.from_concept_id)
        .filter(Concept.topic_id == topic.id)
        .all()
    )
    assert relationships
    # Prerequisite relationships must carry evidence and not be marked verified.
    prereqs = [r for r in relationships if r.relationship_type == "prerequisite_of"]
    assert prereqs, "expected at least one prerequisite relationship"
    assert all(r.evidence for r in prereqs)
    assert all(r.status != VerificationStatus.verified.value for r in prereqs)


def test_build_without_passages_fails(db_session):
    topic = graph_service.create_topic(
        db_session, actor_id=None, org_id=None, name="Empty Topic"
    )
    with pytest.raises(ValidationError):
        graph_service.run_graph_build(
            db_session, topic=topic, extractor=DeterministicExtractor(), created_by=None
        )
    # A failed build must not publish any graph version.
    assert db_session.query(graph_service.GraphVersion).count() == 0


def test_build_with_validation_error_does_not_publish(db_session):
    topic = graph_service.create_topic(
        db_session, actor_id=None, org_id=None, name="Cyclic Topic"
    )
    _seed_document(db_session, topic, PASSAGES)

    class CyclicExtractor:
        name = "cyclic-test"

        def extract(self, topic_name, passages):
            concepts = [
                ConceptDraft(stable_key="a", canonical_name="A"),
                ConceptDraft(stable_key="b", canonical_name="B"),
            ]
            relationships = [
                RelationshipDraft("a", "b", "prerequisite_of", [{"p": 1}], 0.9, "verified"),
                RelationshipDraft("b", "a", "prerequisite_of", [{"p": 1}], 0.9, "verified"),
            ]
            return ExtractionResult(concepts=concepts, relationships=relationships)

    with pytest.raises(ValidationError) as exc_info:
        graph_service.run_graph_build(
            db_session, topic=topic, extractor=CyclicExtractor(), created_by=None
        )
    assert exc_info.value.details["ok"] is False
    # No version should have been published.
    versions = graph_service.list_graph_versions(db_session, topic.id)
    assert versions == []


def test_rollback_creates_new_version_from_snapshot(db_session):
    topic = graph_service.create_topic(
        db_session, actor_id=None, org_id=None, name="Rollback Topic"
    )
    _seed_document(db_session, topic, PASSAGES)
    graph_service.run_graph_build(
        db_session, topic=topic, extractor=DeterministicExtractor(), created_by=None
    )

    restored = graph_service.rollback_to_version(
        db_session, topic=topic, target_version=1, created_by=None
    )
    assert restored.version == 2
    assert restored.validation_report["rolled_back_from"] == 1
    versions = graph_service.list_graph_versions(db_session, topic.id)
    assert len(versions) == 2


def test_graph_versions_are_ordered_and_immutable(db_session):
    topic = graph_service.create_topic(
        db_session, actor_id=None, org_id=None, name="Order Topic"
    )
    _seed_document(db_session, topic, PASSAGES)
    graph_service.run_graph_build(
        db_session, topic=topic, extractor=DeterministicExtractor(), created_by=None
    )
    versions = graph_service.list_graph_versions(db_session, topic.id)
    assert [v.version for v in versions] == [1]
    snapshot = versions[0].snapshot
    assert snapshot["concepts"]
    assert snapshot["relationships"]
