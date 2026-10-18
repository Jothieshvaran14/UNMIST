"""Shared helpers for tests that need a built knowledge graph."""

from __future__ import annotations

import hashlib

from app.models.knowledge import Source, SourceDocument, SourcePassage
from app.services.knowledge import graph_service
from app.services.knowledge.concept_extraction import DeterministicExtractor

DEFAULT_PASSAGES = [
    "Decorators are functions that modify other functions.",
    "First-class functions are functions that can be passed as arguments and returned as values.",
    "Closures are a prerequisite for decorators.",
]


def seed_graph(db_session, *, name="Python Decorators", passages=None):
    passages = passages or DEFAULT_PASSAGES
    topic = graph_service.create_topic(
        db_session, actor_id=None, org_id=None, name=name
    )
    source = Source(
        org_id=None,
        source_type="documentation",
        canonical_url="https://example.com/decorators",
        license_status="permitted",
        publisher="Example",
        title=name,
        extraction_method="deterministic-v1",
        extraction_version="1",
    )
    db_session.add(source)
    db_session.flush()
    document = SourceDocument(
        source_id=source.id,
        topic_id=topic.id,
        title=name,
        url="https://example.com/decorators",
        media_type="text/html",
        content_hash="abc",
        size_bytes=100,
        extracted_text=" ".join(passages),
        status="extracted",
    )
    db_session.add(document)
    db_session.flush()
    for index, passage in enumerate(passages):
        db_session.add(
            SourcePassage(
                document_id=document.id,
                locator=f"passage:{index}",
                text=passage,
                content_hash=hashlib.sha256(passage.encode()).hexdigest(),
            )
        )
    db_session.flush()
    build = graph_service.run_graph_build(
        db_session, topic=topic, extractor=DeterministicExtractor(), created_by=None
    )
    assert build.status == "succeeded", build.error
    return topic
