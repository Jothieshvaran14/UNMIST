"""Knowledge graph API routes.

Long-running graph builds are enqueued as durable jobs; the API returns 202 and
the client polls for status. Ingestion is synchronous but bounded by retrieval
limits.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from ...db import get_db
from ...errors import NotFoundError
from ...models import Topic, User
from ...schemas.common import Page
from ...schemas.knowledge import (
    GraphBuildResponse,
    GraphBuildStatusResponse,
    GraphVersionResponse,
    RollbackRequest,
    SourceIngestRequest,
    SourceIngestResponse,
    TopicCreateRequest,
    TopicResponse,
)
from ...security.dependencies import get_current_user
from ...services import jobs as job_service
from ...services.audit import record_audit
from ...services.knowledge import graph_service, ingestion
from ...services.knowledge.retrieval import RetrievalClient

router = APIRouter(prefix="/topics", tags=["knowledge"])


def _authorize_topic(session: Session, topic: Topic, user: User) -> None:
    """Guard a topic route. Delegates to the canonical service-layer rule.

    Keeping the decision in :func:`graph_service.authorize_topic_access` means
    the learning routes (which resolve questions through their topic) enforce
    exactly the same policy rather than a second copy of it.
    """
    graph_service.authorize_topic_access(session, topic=topic, user=user)


@router.post("", response_model=TopicResponse, status_code=status.HTTP_201_CREATED)
def create_topic(
    payload: TopicCreateRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    # Personal topics (org_id=None) are private to the creator. Organization
    # topics require membership; we default to personal unless org_id is provided.
    org_id = None
    topic = graph_service.create_topic(
        session,
        actor_id=actor.id,
        org_id=org_id,
        name=payload.name,
        description=payload.description,
    )
    session.commit()
    return topic


@router.get("", response_model=Page[TopicResponse])
def list_topics(
    limit: int = 20,
    offset: int = 0,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    from ...api.deps import pagination

    page = pagination(limit=limit, offset=offset)
    # Scoped to the caller: personal topics are the creator's alone, and
    # organization topics are limited to orgs the caller belongs to. Without
    # this the endpoint returned every user's personal topics.
    items, total = graph_service.list_topics(
        session,
        org_id=None,
        limit=page.limit,
        offset=page.offset,
        viewer_id=actor.id,
    )
    return Page.create(
        [TopicResponse.model_validate(item) for item in items], total, page.limit, page.offset
    )


@router.get("/{topic_id}", response_model=TopicResponse)
def get_topic(
    topic_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    topic = graph_service.get_topic(session, topic_id)
    _authorize_topic(session, topic, actor)
    return topic


@router.post(
    "/{topic_id}/sources",
    response_model=SourceIngestResponse,
    status_code=status.HTTP_201_CREATED,
)
def ingest_source(
    topic_id: str,
    payload: SourceIngestRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    topic = graph_service.get_topic(session, topic_id)
    _authorize_topic(session, topic, actor)
    client = RetrievalClient()
    result = ingestion.ingest_url(
        session,
        topic=topic,
        request=ingestion.IngestionRequest(
            url=payload.url,
            source_type=payload.source_type,
            license_status=payload.license_status,
            publisher=payload.publisher,
            title=payload.title,
            license_detail=payload.license_detail,
            access_method=payload.access_method,
            terms_summary=payload.terms_summary,
        ),
        client=client,
    )
    record_audit(
        session,
        action="knowledge.source.ingest",
        actor_id=actor.id,
        org_id=topic.org_id,
        resource_type="source_document",
        resource_id=result["document_id"],
        details={"url": payload.url, "license_status": payload.license_status},
    )
    session.commit()
    return SourceIngestResponse(**result)


@router.post(
    "/{topic_id}/builds",
    response_model=GraphBuildResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_graph_build(
    topic_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    topic = graph_service.get_topic(session, topic_id)
    _authorize_topic(session, topic, actor)
    from ...models.knowledge import GraphBuild

    build = GraphBuild(topic_id=topic.id, status="queued", trigger="manual", created_by=actor.id)
    session.add(build)
    session.flush()
    job = job_service.enqueue(
        session,
        job_type="graph.build",
        payload={"topic_id": topic.id, "created_by": actor.id, "build_id": build.id},
        tenant_id=topic.org_id,
        idempotency_key=f"graph.build:{topic.id}:{build.id}",
    )
    record_audit(
        session,
        action="knowledge.graph.build_enqueued",
        actor_id=actor.id,
        org_id=topic.org_id,
        resource_type="graph_build",
        resource_id=build.id,
        details={"job_id": job.id},
    )
    session.commit()
    return GraphBuildResponse(build_id=build.id, job_id=job.id, status=job.status)


@router.get("/{topic_id}/builds/{build_id}", response_model=GraphBuildStatusResponse)
def get_graph_build(
    topic_id: str,
    build_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    from ...models.knowledge import GraphBuild

    topic = graph_service.get_topic(session, topic_id)
    _authorize_topic(session, topic, actor)
    build = session.get(GraphBuild, build_id)
    if build is None or build.topic_id != topic.id:
        raise NotFoundError("Build not found")
    return build


@router.get("/{topic_id}/graphs", response_model=list[GraphVersionResponse])
def list_graph_versions(
    topic_id: str,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    topic = graph_service.get_topic(session, topic_id)
    _authorize_topic(session, topic, actor)
    return graph_service.list_graph_versions(session, topic.id)


@router.get("/{topic_id}/graphs/{version}", response_model=GraphVersionResponse)
def get_graph_version(
    topic_id: str,
    version: int,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    topic = graph_service.get_topic(session, topic_id)
    _authorize_topic(session, topic, actor)
    return graph_service.get_graph_version(session, topic.id, version)


@router.post("/{topic_id}/graphs/rollback", response_model=GraphVersionResponse)
def rollback_graph(
    topic_id: str,
    payload: RollbackRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    topic = graph_service.get_topic(session, topic_id)
    _authorize_topic(session, topic, actor)
    restored = graph_service.rollback_to_version(
        session, topic=topic, target_version=payload.target_version, created_by=actor.id
    )
    record_audit(
        session,
        action="knowledge.graph.rollback",
        actor_id=actor.id,
        org_id=topic.org_id,
        resource_type="graph_version",
        resource_id=restored.id,
        details={"target_version": payload.target_version, "new_version": restored.version},
    )
    session.commit()
    return restored
