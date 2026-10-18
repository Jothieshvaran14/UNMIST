"""Background job handlers.

Importing this module registers all handlers with the job service.
"""

from __future__ import annotations

from ...models.knowledge import Topic
from ...services import jobs
from ...services.knowledge import concept_extraction, graph_service


@jobs.register_handler("graph.build")
def handle_graph_build(session, job) -> None:
    topic_id = job.payload.get("topic_id")
    if not topic_id:
        raise ValueError("graph.build job is missing 'topic_id'")
    topic = session.get(Topic, topic_id)
    if topic is None:
        raise ValueError(f"Topic '{topic_id}' no longer exists")
    extractor = concept_extraction.get_extractor()
    graph_service.run_graph_build(
        session,
        topic=topic,
        extractor=extractor,
        created_by=job.payload.get("created_by"),
        trigger="job",
    )
