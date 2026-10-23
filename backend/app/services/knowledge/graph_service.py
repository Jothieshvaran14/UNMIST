"""Knowledge graph build, validation, publication, and versioning.

A build is a unit of work that turns registered source documents into a
validated, immutable graph version. Validation failures prevent publication:
unsupported relationships are never published as verified.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from ...errors import AuthorizationError, NotFoundError, ValidationError
from ...models.knowledge import (
    Claim,
    Concept,
    ConceptRelationship,
    ConceptVersion,
    GraphBuild,
    GraphBuildStatus,
    GraphVersion,
    SourceDocument,
    SourcePassage,
    Topic,
    VerificationStatus,
)
from ...models.org import OrgMembership
from .concept_extraction import ConceptExtractor, ExtractionResult
from .validation import (
    ConceptDraft,
    RelationshipDraft,
    build_snapshot,
    validate_graph,
)


def slugify_topic(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return re.sub(r"-+", "-", slug)[:200] or "topic"


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------
def create_topic(
    session: Session,
    *,
    actor_id: Optional[str],
    org_id: Optional[str],
    name: str,
    description: Optional[str] = None,
) -> Topic:
    base = slugify_topic(name)
    slug = base
    suffix = 1
    while (
        session.execute(
            select(Topic).where(Topic.org_id == org_id, Topic.slug == slug)
        ).scalar_one_or_none()
        is not None
    ):
        suffix += 1
        slug = f"{base}-{suffix}"
    topic = Topic(
        org_id=org_id,
        name=name.strip(),
        slug=slug,
        description=description,
        created_by=actor_id,
    )
    session.add(topic)
    session.flush()
    return topic


def get_topic(session: Session, topic_id: str) -> Topic:
    topic = session.get(Topic, topic_id)
    if topic is None:
        raise NotFoundError("Topic not found")
    return topic


def authorize_topic_access(session: Session, *, topic: Topic, user) -> None:
    """Enforce who may read or write a topic.

    This is the single canonical rule for topic visibility, shared by the
    knowledge routes and the learning (question/assessment) routes so the two
    can never drift apart:

      * a personal topic (``org_id IS NULL``) is private to its creator;
      * an organization topic is readable/writable by its members only.

    Questions inherit their visibility from their topic, so every question or
    rubric lookup must resolve through here. Without that hop a bare
    ``session.get`` would let any authenticated caller read another tenant's
    ``reference_answer``.
    """
    if topic.org_id is None:
        if topic.created_by != user.id:
            raise AuthorizationError("You do not have access to this topic")
        return
    membership = session.execute(
        select(OrgMembership).where(
            OrgMembership.org_id == topic.org_id,
            OrgMembership.user_id == user.id,
        )
    ).scalar_one_or_none()
    if membership is None:
        raise AuthorizationError("You are not a member of this organization")


def _viewer_visibility(topic_alias, viewer_id: str):
    """SQL fragment matching :func:`authorize_topic_access` for one viewer.

    Kept next to the authorization rule so the query filter and the guard
    cannot silently disagree: personal topics are visible only to their
    creator, organization topics only to members of that organization.
    """
    member_orgs = (
        select(OrgMembership.org_id)
        .where(OrgMembership.user_id == viewer_id)
        .scalar_subquery()
    )
    return or_(
        and_(topic_alias.org_id.is_(None), topic_alias.created_by == viewer_id),
        topic_alias.org_id.in_(member_orgs),
    )


def list_topics(
    session: Session,
    *,
    org_id: Optional[str],
    limit: int,
    offset: int,
    viewer_id: Optional[str] = None,
):
    """List topics, scoped to what ``viewer_id`` is actually allowed to see.

    ``viewer_id`` is required in practice: passing ``None`` would widen the
    result set to every personal topic of every user, so the parameter is
    treated as mandatory whenever a filter is wanted. ``org_id`` narrows
    further to a single organization once visibility is established.
    """
    scope = []
    if viewer_id is not None:
        scope.append(_viewer_visibility(Topic, viewer_id))
    if org_id is not None:
        scope.append(Topic.org_id == org_id)

    count_query = select(func.count()).select_from(Topic)
    query = select(Topic)
    for clause in scope:
        count_query = count_query.where(clause)
        query = query.where(clause)

    total = session.execute(count_query).scalar_one()
    items = (
        session.execute(
            query.order_by(Topic.created_at.desc()).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )
    return list(items), int(total)


# ---------------------------------------------------------------------------
# Builds
# ---------------------------------------------------------------------------
def run_graph_build(
    session: Session,
    *,
    topic: Topic,
    extractor: ConceptExtractor,
    created_by: Optional[str] = None,
    trigger: str = "manual",
) -> GraphBuild:
    build = GraphBuild(
        topic_id=topic.id,
        status=GraphBuildStatus.running.value,
        trigger=trigger,
        created_by=created_by,
        started_at=datetime.now(timezone.utc),
    )
    session.add(build)
    session.flush()

    try:
        passages = _load_passages(session, topic.id)
        if not passages:
            raise ValidationError(
                "No source passages are available for this topic. Register and extract "
                "permitted sources before building a graph."
            )
        result = extractor.extract(topic.name, passages)
        if not result.concepts:
            raise ValidationError(
                "No concepts could be extracted from the available evidence. The graph "
                "was not published."
            )
        report = validate_graph(result.concepts, result.relationships)
        if not report.ok:
            raise _GraphValidationError(report)

        _publish(session, topic, build, result, report)
        build.status = GraphBuildStatus.succeeded.value
        build.error = None
        session.commit()
        return build
    except Exception as exc:
        build.status = GraphBuildStatus.failed.value
        build.error = str(exc)[:2000]
        build.finished_at = datetime.now(timezone.utc)
        session.commit()
        if isinstance(exc, _GraphValidationError):
            raise ValidationError(
                str(exc), code="graph_validation_failed", details=exc.report.to_dict()
            ) from exc
        raise
    finally:
        build.finished_at = datetime.now(timezone.utc)
        session.flush()


class _GraphValidationError(Exception):
    def __init__(self, report) -> None:
        super().__init__("Graph validation failed")
        self.report = report


def _load_passages(session: Session, topic_id: str) -> list[tuple[str, str]]:
    rows = session.execute(
        select(SourcePassage.id, SourcePassage.text)
        .join(SourceDocument, SourceDocument.id == SourcePassage.document_id)
        .where(SourceDocument.topic_id == topic_id)
        .order_by(SourcePassage.locator)
    ).all()
    return [(row[0], row[1]) for row in rows]


def _next_concept_version(session: Session, concept_id: str) -> int:
    current = session.execute(
        select(func.coalesce(func.max(ConceptVersion.version), 0)).where(
            ConceptVersion.concept_id == concept_id
        )
    ).scalar_one()
    return int(current) + 1


def _next_graph_version(session: Session, topic_id: str) -> int:
    current = session.execute(
        select(func.coalesce(func.max(GraphVersion.version), 0)).where(
            GraphVersion.topic_id == topic_id
        )
    ).scalar_one()
    return int(current) + 1


def _upsert_concept(session: Session, topic_id: str, draft: ConceptDraft) -> Concept:
    concept = session.execute(
        select(Concept).where(
            Concept.topic_id == topic_id, Concept.stable_key == draft.stable_key
        )
    ).scalar_one_or_none()
    fields = {
        "canonical_name": draft.canonical_name,
        "aliases": list(draft.aliases),
        "definition": draft.definition,
        "explanation": draft.explanation,
        "misconceptions": list(draft.misconceptions),
        "difficulty": draft.difficulty,
        "learning_objectives": list(draft.learning_objectives),
        "verification_status": VerificationStatus.unverified.value,
    }
    if concept is None:
        concept = Concept(topic_id=topic_id, stable_key=draft.stable_key, **fields)
        session.add(concept)
        session.flush()
    else:
        for key, value in fields.items():
            setattr(concept, key, value)
        concept.last_verified_at = None
        session.flush()
    return concept


def _snapshot_concept(concept: Concept) -> dict:
    return {
        "stable_key": concept.stable_key,
        "canonical_name": concept.canonical_name,
        "aliases": list(concept.aliases),
        "definition": concept.definition,
        "explanation": concept.explanation,
        "misconceptions": list(concept.misconceptions),
        "difficulty": concept.difficulty,
        "learning_objectives": list(concept.learning_objectives),
        "verification_status": concept.verification_status,
    }


def _upsert_relationship(
    session: Session, draft: RelationshipDraft, key_map: dict
) -> ConceptRelationship:
    from_concept = key_map[draft.from_key]
    to_concept = key_map[draft.to_key]
    rel = session.execute(
        select(ConceptRelationship).where(
            ConceptRelationship.from_concept_id == from_concept.id,
            ConceptRelationship.to_concept_id == to_concept.id,
            ConceptRelationship.relationship_type == draft.relationship_type,
        )
    ).scalar_one_or_none()
    if rel is None:
        rel = ConceptRelationship(
            from_concept_id=from_concept.id,
            to_concept_id=to_concept.id,
            relationship_type=draft.relationship_type,
        )
        session.add(rel)
    rel.evidence = list(draft.evidence)
    rel.confidence = draft.confidence
    rel.status = draft.status
    session.flush()
    return rel


def _publish(
    session: Session, topic: Topic, build: GraphBuild, result: ExtractionResult, report
) -> GraphVersion:
    key_map: dict[str, Concept] = {}

    # Replace claims for this topic to keep builds idempotent.
    concept_ids: list[str] = []
    for draft in result.concepts:
        concept = _upsert_concept(session, topic.id, draft)
        key_map[draft.stable_key] = concept
        concept_ids.append(concept.id)
        session.add(
            ConceptVersion(
                concept_id=concept.id,
                version=_next_concept_version(session, concept.id),
                snapshot=_snapshot_concept(concept),
            )
        )

    if concept_ids:
        session.execute(delete(Claim).where(Claim.concept_id.in_(concept_ids)))

    for draft in result.concepts:
        concept = key_map[draft.stable_key]
        for claim in draft.claims:
            session.add(
                Claim(
                    concept_id=concept.id,
                    passage_id=claim.passage_id,
                    claim_type=claim.claim_type,
                    statement=claim.statement,
                    source_span=claim.source_span,
                    confidence=claim.confidence,
                    status=claim.status,
                )
            )

    for rel_draft in result.relationships:
        _upsert_relationship(session, rel_draft, key_map)

    version = _next_graph_version(session, topic.id)
    graph_version = GraphVersion(
        build_id=build.id,
        topic_id=topic.id,
        version=version,
        status="published",
        snapshot=build_snapshot(result.concepts, result.relationships),
        validation_report=report.to_dict(),
        concept_count=len(result.concepts),
        relationship_count=len(result.relationships),
    )
    session.add(graph_version)
    session.flush()
    return graph_version


def get_graph_version(session: Session, topic_id: str, version: int) -> GraphVersion:
    graph_version = session.execute(
        select(GraphVersion).where(
            GraphVersion.topic_id == topic_id, GraphVersion.version == version
        )
    ).scalar_one_or_none()
    if graph_version is None:
        raise NotFoundError(f"Graph version {version} not found")
    return graph_version


def list_graph_versions(session: Session, topic_id: str) -> list[GraphVersion]:
    return list(
        session.execute(
            select(GraphVersion)
            .where(GraphVersion.topic_id == topic_id)
            .order_by(GraphVersion.version.desc())
        ).scalars().all()
    )


def rollback_to_version(
    session: Session, *, topic: Topic, target_version: int, created_by: Optional[str] = None
) -> GraphVersion:
    """Publish a new version that restores an earlier immutable snapshot."""
    target = get_graph_version(session, topic.id, target_version)
    build = GraphBuild(
        topic_id=topic.id,
        status=GraphBuildStatus.succeeded.value,
        trigger="rollback",
        created_by=created_by,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
    )
    session.add(build)
    session.flush()

    concepts: list[ConceptDraft] = [
        ConceptDraft(
            stable_key=item["stable_key"],
            canonical_name=item["canonical_name"],
            aliases=item.get("aliases", []),
            definition=item.get("definition"),
            explanation=item.get("explanation"),
            misconceptions=item.get("misconceptions", []),
            difficulty=item.get("difficulty"),
            learning_objectives=item.get("learning_objectives", []),
            claims=[],
        )
        for item in target.snapshot.get("concepts", [])
    ]
    relationships: list[RelationshipDraft] = [
        RelationshipDraft(
            from_key=item["from_key"],
            to_key=item["to_key"],
            relationship_type=item["relationship_type"],
            evidence=item.get("evidence", []),
            confidence=item.get("confidence", 0.0),
            status=item.get("status", VerificationStatus.unverified.value),
        )
        for item in target.snapshot.get("relationships", [])
    ]

    key_map: dict[str, Concept] = {}
    for draft in concepts:
        concept = _upsert_concept(session, topic.id, draft)
        key_map[draft.stable_key] = concept
        session.add(
            ConceptVersion(
                concept_id=concept.id,
                version=_next_concept_version(session, concept.id),
                snapshot=_snapshot_concept(concept),
            )
        )
    for rel_draft in relationships:
        _upsert_relationship(session, rel_draft, key_map)

    next_version = _next_graph_version(session, topic.id)
    restored = GraphVersion(
        build_id=build.id,
        topic_id=topic.id,
        version=next_version,
        status="published",
        snapshot=dict(target.snapshot),
        validation_report={
            "ok": True,
            "rolled_back_from": target_version,
            "note": "Restored from an immutable earlier version.",
        },
        concept_count=len(concepts),
        relationship_count=len(relationships),
    )
    session.add(restored)
    session.flush()
    return restored
