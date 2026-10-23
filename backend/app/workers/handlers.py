"""Background job handlers.

Importing this module registers all handlers with the job service.
"""

from __future__ import annotations

from ..models.knowledge import Topic
from ..services import jobs
from ..services.documents.ingestion import (
    DOCUMENT_OCR_JOB,
    DOCUMENT_PROCESS_JOB,
    DOCUMENT_SEGMENT_JOB,
)
from ..services.knowledge import concept_extraction, graph_service


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


@jobs.register_handler(DOCUMENT_PROCESS_JOB)
def handle_document_process(session, job) -> None:
    """Run page preprocessing for an uploaded document.

    The heavy lifting lives in ``DocumentPipeline`` so its transaction
    boundaries, lease checks, and idempotency are testable in isolation. Any
    exception propagates to the queue's retry machinery; permanent input
    errors raise ``PermanentJobError`` to fail fast without burning retries.
    """
    from ..services.documents.pipeline import DocumentPipeline

    DocumentPipeline(session, job).run()


@jobs.register_handler(DOCUMENT_OCR_JOB)
def handle_document_ocr(session, job) -> None:
    """Run handwriting recognition for a preprocessed document.

    Kept separate from ``document.process``: OCR needs an optional ML stack and
    a loaded model, and must never block plain page preparation. Provider
    selection is lazy inside the pipeline, so a skipped or cancelled job never
    loads a model. ``PermanentJobError`` marks capability failures (no provider,
    no model artifact) dead immediately rather than burning the retry budget.
    """
    from ..services.documents.ocr_pipeline import DocumentOcrPipeline

    DocumentOcrPipeline(session, job).run()


@jobs.register_handler(DOCUMENT_SEGMENT_JOB)
def handle_document_segment(session, job) -> None:
    """Derive question/answer candidates from recognized regions.

    Pure rules over already-persisted OCR regions: no model, no download, no
    network, and no LLM (``LLM_PROVIDER=none`` remains the default). Kept
    separate from OCR so structure extraction can be requested and re-run on a
    document whose ``processing_state`` is already terminal.
    """
    from ..services.documents.segmentation_pipeline import (
        DocumentSegmentationPipeline,
    )

    DocumentSegmentationPipeline(session, job).run()
