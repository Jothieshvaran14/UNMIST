# Architecture

Unmist is a **modular monolith**: a single deployable FastAPI application with
strict internal layering. This keeps operational simplicity while preserving
clean seams for later extraction into services.

## Layering

```
HTTP  →  api/v1/*        (routers: auth, orgs, knowledge, learning, profiles, health)
            ↓             (auth + RBAC dependencies in security/)
       services/*        (business logic; the only place transactions are managed)
            ↓
       models/*          (SQLAlchemy 2.0 ORM; 27 tables)
            ↓
       db.py             (engine/session; SQLite dev, PostgreSQL prod)
```

Rules:

1. **Routers never touch the DB directly.** They validate input (Pydantic),
   resolve the caller and tenant, then call a service.
2. **Services own transactions.** A service method is the unit of work.
3. **Models carry no business logic** beyond constraints and relationships.
4. **Cross-cutting concerns** (logging, audit, errors) live in `core`-level
   modules and are composed in, not imported ad hoc.

## Components

### Knowledge pipeline

```
retrieval.py ──▶ extraction.py ──▶ ingestion.py ──▶ graph_service.py ──▶ validation.py
 (SSRF-safe       (deterministic    (license          (build, publish      (cycles, dupes,
  fetch)           baseline;         enforcement)      immutable versions,  evidence,
                  LLM boundary                         rollback)            orphans)
                  fail-loud)
```

- **Retrieval** is deny-by-default: an empty host allowlist blocks all egress.
  Private/loopback/link-local IPs are rejected after DNS resolution, redirects
  are re-checked per hop, and robots.txt is honored via a self-contained parser.
- **Extraction** is deterministic by default (sentence segmentation + keyword
  extraction). The LLM path is a separate boundary that raises when unconfigured
  so no model output can silently become "verified" content.
- **Graph service** publishes immutable versions; rollback re-points to an
  earlier version rather than mutating history.

### Learning pipeline

```
questions/rubrics ──▶ checks.py ──▶ grading.py ──▶ mastery.py (BKT)
                                              └──▶ diagnosis.py (prerequisite gaps)
                                              └──▶ scheduling.py (spaced retention)
```

- **Checks** are deterministic and side-effect free except the code check,
  which runs in `sandbox.py`.
- **Mastery** is Bayesian Knowledge Tracing with fixed, **uncalibrated**
  parameters (marked `provisional-v1`). With too little evidence it reports
  `insufficient_evidence` instead of a confident probability.
- **Diagnosis** builds competing hypotheses from published graph prerequisites
  and picks the next question by an information-gain heuristic. It never
  invents a prerequisite that is not in the graph.

### Jobs & workers

A **durable, DB-backed queue** (no external broker) runs long work out-of-band:

- `services/jobs.py` — enqueue (idempotent), claim (sets `running`/`started_at`,
  increments `attempts`), lease reclamation of abandoned jobs, retry with
  backoff, dead-letter after max attempts, and `PermanentJobError` to fail
  malformed input fast without burning retries.
- `workers/handlers.py` — a registry of job handlers (`graph.build`,
  `document.process`).
- `workers/runner.py` — a poll loop that claims and executes jobs.

This keeps the HTTP path fast and makes graph builds and document processing
restartable.

### Document processing pipeline (Checkpoint C)

An uploaded document moves through a **recoverable, page-preparation**
workflow. This is explicitly **not** OCR — a successful run means page images
are ready, never that text was recognized.

- `services/documents/validation.py` — magic-byte + real-parser upload
  validation (format decided by bytes, not client MIME/filename).
- `services/storage.py` (`LocalObjectStore`) — private, immutable,
  hash-verified object storage with app-generated opaque keys (no filenames,
  no public URLs).
- `services/documents/state.py` — the validated state machine
  (`uploaded → queued → preprocessing → preprocessed → ocr_processing → …`).
  The `preprocessed` state exists so a prepared-but-unrecognized document is
  never mistaken for a recognized one.
- `services/documents/preprocess.py` — bounded image/PDF normalization:
  EXIF orientation, downscale-only resize, grayscale, blank-page flag, PDF
  rasterization via an optional permissive-licensed backend (`pypdfium2`).
  Enforces page-count, dimension, per-page-time, and job-time limits. Preserves
  the original↔working coordinate mapping via `DocumentPage.working_scale`.
- `services/documents/pipeline.py` (`DocumentPipeline`) — the `document.process`
  handler. Uses short transactions (never holds a DB transaction across
  rendering), idempotent page/artifact writes keyed by
  `(document, page_number, content_hash)`, a lease-ownership check so a stale
  worker cannot finalize, cooperative cancellation between pages, and
  retryable-vs-permanent failure classification.

The pipeline stores **page images and geometry only**. OCR, handwriting
recognition, equation recognition, and question segmentation are later
checkpoints and are not implemented here.

### Document OCR pipeline (Checkpoint D)

Recognition is a **separate, optional job** (`document.ocr`) that runs *after*
preprocessing, so the base install and ordinary CI never pay for the ML stack.

- `services/documents/ocr/base.py` — the provider-neutral contract:
  `OcrProvider`, `OcrPageInput` (bytes + geometry, no storage keys/paths),
  `OcrRegionOutput`, `OcrPageResult` (records `scope="text_lines"`,
  `empty_reason`, `partial`), and `OcrError` (bounded category + permanent
  flag). TrOCR emits no calibrated confidence, so regions report `NULL`.
- `services/documents/ocr/none.py` / `fake.py` — the `none` provider fails
  explicitly (never invents text); the `fake` provider is a deterministic test
  double, refused outright when `ENVIRONMENT=production`.
- `services/documents/ocr/lines.py` — Pillow-only horizontal-projection line
  detection (no numpy/OpenCV/ML). Returns `[]` for a blank page (never guesses
  geometry) and maps boxes back to original-image space, clamped to page
  bounds. Documented boundary: single-column upright text only; a future layout
  component replaces this module.
- `services/documents/ocr/trocr.py` — the real adapter. **Lazy** (torch and
  transformers are imported and the model is loaded on first use, never at
  import or startup), **load-once** under a process lock, **CPU-first** (CUDA
  only when actually available; an unavailable explicit CUDA request is a clear
  permanent error, not a silent downgrade). Per-page/job budgets are enforced
  *between* crops because a native `model.generate` cannot be interrupted.
- `services/documents/ocr/normalization.py` — explicit, versioned
  (`ocr-norm-v1`) whitespace-only normalization that never strips signs,
  decimals, math, units, or punctuation; `raw_text` is always kept verbatim.
- `services/documents/ocr/registry.py` — lazy, per-process-cached provider
  construction under a lock; construction failures become a permanent
  `ocr_unavailable` error.
- `services/documents/ocr_pipeline.py` (`DocumentOcrPipeline`) — the
  `document.ocr` handler. Mirrors `DocumentPipeline`: short transactions
  (state committed before inference, each page's regions committed as written),
  a lease-ownership check before finalize, cooperative cancellation between
  pages, and retryable-vs-permanent classification. Regions are persisted
  idempotently by a deterministic `region_uid` (derived from
  provider+model+processing_version+reading order) under a unique
  `(page_id, region_uid)` constraint; a row whose `review_status` is no longer
  `pending` is never overwritten, so reviewer corrections survive retries.
  Partial work is never reported as success: a page that produces nothing
  records why, and a document that fails partway lands in `review_required` or
  `failed`, never `completed`.

The pipeline records **line-level recognition results with provenance**. It does
not detect questions, reconstruct layout, read tables/diagrams, or grade.

- `services/evaluation/` (`cer.py`, `wer.py`, `report.py`) — reproducible
  CER/WER (Levenshtein-based) with a documented empty-reference policy and
  JSON reports that record the full evaluation configuration. Reports built
  from the `fake` provider are stamped `is_real_model: false`. Real-model
  scoring via `scripts/evaluate_ocr.py` is refused unless a dataset license is
  supplied, so accuracy numbers cannot be produced without stating what
  lawfully permits their use.

### Document segmentation pipeline (Checkpoint E)

Segmentation is a **third, independent stage** that reads the regions the OCR
stage persisted and turns them into reviewable question/answer structure. It is
pure rules — no model, no download, no LLM (`LLM_PROVIDER=none` remains the
default) — and it never mutates `OcrRegion`.

Why it has its own status axis: `processing_state` describes preprocessing/OCR
and `completed` is terminal *there*. Segmentation must be requestable,
re-runnable, and fail-able after OCR has finished, so `source_documents` carries
`segmentation_status`, `segmentation_version`, `segmentation_error_category`,
`segmentation_error_detail`, `last_segment_job_id`, and `segmentation_run`
instead of new `processing_state` values. The OCR state machine is untouched.

- `services/documents/segmentation/base.py` — shared vocabulary with no
  persistence/FastAPI/ML imports: `RegionInput`/`PageInput` (geometry + text
  only, never storage keys or paths), `SegmentCandidate`, `SegmentationResult`,
  `SegmentLimits` (page/region/segment/text caps), `SegmentationError`
  (bounded category + permanent flag), and `derive_segment_uid`.
- `services/documents/segmentation/rules.py` — every pattern and threshold in
  one versioned place (`SegmentationRules`, `SEGMENT_ALGORITHM_VERSION`).
  Heuristics are conservative by construction: an answer marker needs a real
  separator, a caption needs `:/.`-separated label, a table needs pipes or a
  captioned `Table N:`, and a formula line is rejected when it reads as prose.
- `services/documents/segmentation/segmenter.py` (`BaselineSegmenter`) —
  deterministic: input is sanitized and sorted (page, reading order, position,
  original index), page furniture is detected from repetition plus positional
  evidence, then body blocks are walked in reading order. It emits a
  **categorical** `SegmentUncertainty` per segment (`clear`,
  `ambiguous_boundary`, `unclassified`, `suspected_continuation`,
  `conflicting_numbering`, `missing_ocr`) — never a numeric score — and
  guarantees total coverage: every input region lands in exactly one segment.
  The segment box is an **anchor box on `start_page` only**; cross-page boxes
  are never merged, and per-region page-local geometry stays on `OcrRegion`
  via `segment_regions`.
- `services/documents/segmentation_pipeline.py` (`DocumentSegmentationPipeline`)
  — the `document.segment` handler, mirroring `DocumentOcrPipeline`: state
  committed before the work, candidates derived *outside* any transaction,
  persistence in short committed chunks with lease and cancellation checks at
  each boundary, and retryable-vs-permanent classification. Segments are keyed
  by a deterministic `segment_uid` (sha256 of first region + type; text- and
  version-independent) under a unique `(document_id, segment_uid)`, so redelivery
  and explicit reprocessing update rather than duplicate. A row whose
  `review_status` left `pending` is never overwritten, never pruned, and its
  regions are never reassigned; stale *pending* rows are pruned **before** the
  insert pass so `segment_regions.region_id` (unique) can never be double
  claimed. Partial outcomes are never reported as completion: a lost lease or a
  document that goes away mid-run leaves the status at `processing`/`cancelled`,
  not `completed`.

The API layer (`api/v1/documents.py`) exposes four owner-scoped, audited
endpoints — `POST /documents/{id}/segment`, `GET .../segmentation`,
`GET .../segments`, and `POST .../segments/{sid}/review` — each of which
enforces ownership server-side and returns 404 (not 403) for another tenant so
ids cannot be probed.

**What segmentation is not:** it is structure inference over recognized text.
It does not judge whether an answer is correct (that is assessment, out of
scope), it does not change OCR evidence, and no heuristic it uses is claimed to
be accurate on real documents.

## Data model (33 tables)

Grouped by domain:

- **Identity:** `users`, `refresh_tokens`, `organizations`, `organization_members`
- **Audit:** `audit_events`
- **Knowledge:** `sources`, `source_documents`, `source_passages`, `topics`,
  `concepts`, `concept_versions`, `concept_relationships`, `claims`,
  `graph_builds`, `graph_versions`, `learning_objectives`
- **Document processing:** `document_pages`, `ocr_regions`, `ocr_reviews`
- **Segmentation:** `document_segments`, `segment_regions`, `segment_reviews`
- **Learning:** `questions`, `rubrics`, `assessment_attempts`,
  `criterion_results`, `mastery_estimates`, `diagnoses`
- **Retention:** `retention_schedules`, `review_attempts`
- **Portfolio:** `profiles`, `portfolio_items`
- **Jobs:** `jobs`

Schema changes go through **Alembic** (`migrations/versions/`). Migrations are
verified against SQLite by `scripts/verify_migration.py`.

## Cross-cutting concerns

- **Config** (`config.py`) — env-driven, validated at import; unsafe values
  (placeholder `SECRET_KEY` in prod, etc.) abort startup.
- **Logging** (`logging.py`) — JSON logs with a per-request correlation ID.
- **Errors** (`errors.py`) — an `AppError` hierarchy mapped to consistent HTTP
  responses by handlers.
- **Audit** (`services/audit.py`) — append-only events with secret redaction.

## Topic & question authorization (Checkpoint F refactor)

Topic visibility is now decided in **one** place,
`services/knowledge/graph_service.py::authorize_topic_access`:

- a personal topic (`org_id IS NULL`) is private to its creator;
- an organization topic is readable/writable by its members only.

`api/v1/knowledge.py::_authorize_topic` and the question/assessment routes in
`api/v1/learning.py` both delegate to it, so the two families cannot drift. The
checkpoint moved the rule here precisely because they had drifted: the knowledge
routes enforced it, while `GET /questions/{id}` was a bare `session.get` that
disclosed `reference_answer` cross-tenant.

`list_topics` takes a `viewer_id` and applies the equivalent SQL predicate
(`_viewer_visibility`), keeping the query filter and the guard on the same
definition. Questions resolve through their topic, so every question, rubric and
assessment route inherits the topic's policy.

## Workflow completeness (Checkpoint F audit)

Traced end to end, the document pipeline is **not** a closed loop:

| Stage | Reached how |
| --- | --- |
| Upload → validation → private storage → row + `document.process` | API (`POST /documents/uploads`) |
| Preprocessing | `document.process` handler, auto-enqueued |
| **OCR** | API (`POST /documents/{id}/ocr`) + `document.ocr` handler |
| Segmentation scheduling | API (`POST /documents/{id}/segment`) |
| Segmentation → retrieval → review | API + `document.segment` handler |
| **Deletion / cleanup** | API (`DELETE /documents/{id}`) + `document.purge` handler — moves to `deletion_pending`, purges this document's storage objects (before their rows, so a retry still finds the keys), then writes the `deleted` tombstone |

The state machine and every skip-guard for deletion are implemented and tested;
the entry points are `POST /documents/{id}/ocr` and `DELETE /documents/{id}`,
both owner-scoped and audited (`document.ocr_requested`,
`document.delete_requested`, `document.purged`).

## Persistence & dialects

SQLAlchemy 2.0 with a `UTCDateTime` type decorator normalizes datetimes so
SQLite (naive) and PostgreSQL (aware) behave identically. SQLite enables
foreign-key enforcement and WAL for local dev/tests; PostgreSQL uses normal
pooling. **Only SQLite is exercised in this environment** — PostgreSQL is
supported by the ORM layer but not verified here.

## What is intentionally out of scope (for now)

Frontend, equation *recognition*, answer reconstruction, grading-of-handwriting,
a configured LLM, CI/CD, a hardened multi-tenant code sandbox, and PostgreSQL
validation. (Line-level OCR is implemented — see Checkpoint D — and
question/answer *structure* segmentation is implemented as a rule baseline —
see Checkpoint E. Page-layout understanding, formula parsing/solving, and
judging whether an answer is correct are not.) Each has a fail-loud or
clearly-marked boundary; see
[`docs/product/feature-status.md`](product/feature-status.md).
