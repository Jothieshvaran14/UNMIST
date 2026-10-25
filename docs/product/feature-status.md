# Feature Status

Honest, per-feature status. "Working" means there is code **and** passing tests
that exercise it. Nothing is listed as working without a test.

Legend:

- ✅ **Working** — implemented and covered by passing tests.
- ⚠️ **Provisional / baseline** — implemented and tested, but with a documented
  limitation (uncalibrated model, non-hardened isolation, etc.).
- ❌ **Not implemented** — the boundary exists and fails loudly; no functionality
  is claimed.

Last verified: full backend suite **262 passed, 1 skipped** (opt-in real-model
smoke test) on Python 3.10 / SQLite.

---

## 1. Authentication & accounts

| Feature | Status | Evidence |
| --- | --- | --- |
| Register (email + password) | ✅ | `test_auth.py` |
| Login → access + refresh tokens | ✅ | `test_auth.py` |
| Argon2id password hashing | ✅ | `security/passwords.py`, `test_auth.py` |
| Access token (HS256 JWT, short TTL) | ✅ | `security/tokens.py` |
| Opaque rotating refresh tokens (hashed at rest) | ✅ | `test_auth.py` |
| Refresh rotation + reuse detection | ✅ | `test_auth.py` |
| Logout (refresh revocation) | ✅ | `test_auth.py` |

## 2. Organizations & authorization

| Feature | Status | Evidence |
| --- | --- | --- |
| Create organization | ✅ | orgs API + service |
| Membership with roles (owner/admin/member) | ✅ | `security/rbac.py` |
| Server-side authorization on every org route | ✅ | `OrgRole` dependency |
| Member management (add/remove/list) | ✅ | orgs API |

## 3. Knowledge ingestion & provenance

| Feature | Status | Evidence |
| --- | --- | --- |
| Source registration with license metadata | ✅ | `sources.py` |
| License enforcement at ingestion | ✅ | ingestion blocked when license forbids it |
| SSRF-safe HTTP retrieval | ✅ | `test_retrieval.py` |
| Host allowlist (empty = deny all) | ✅ | `test_retrieval.py` |
| Private/loopback/link-local IP blocking | ✅ | `test_retrieval.py` |
| robots.txt respect (self-contained parser) | ✅ | `test_retrieval.py` |
| Size, redirect, and time limits | ✅ | `test_retrieval.py` |
| Injectable transport + resolver (testable) | ✅ | `test_retrieval.py` |
| Document + passage storage with content hashing | ✅ | `extraction.py`, models |
| Deterministic extraction (sentence/keyword baseline) | ✅ | `test_extraction.py` |
| Claim extraction with passage citation | ✅ | `test_extraction.py` |
| LLM extraction | ❌ | `LLM_PROVIDER=none`; `UnconfiguredLLMExtractor` raises |

## 4. Knowledge graph

| Feature | Status | Evidence |
| --- | --- | --- |
| Graph build (job-driven) | ✅ | `test_graph_service.py` |
| Concept + relationship upsert with stable keys | ✅ | `test_graph_service.py` |
| Validation: cycle detection | ✅ | `test_validation.py` |
| Validation: duplicate detection | ✅ | `test_validation.py` |
| Validation: evidence requirement | ✅ | `test_validation.py` |
| Validation: orphan detection | ✅ | `test_validation.py` |
| Immutable published graph versions | ✅ | `test_graph_service.py` |
| Rollback to a prior version | ✅ | `test_graph_service.py` |
| Version listing / diffing | ✅ | `test_graph_service.py` |

## 5. Jobs & workers

| Feature | Status | Evidence |
| --- | --- | --- |
| Durable DB-backed queue | ✅ | `services/jobs.py` |
| Idempotent enqueue | ✅ | `services/jobs.py` |
| Claim sets `running` + `started_at`, increments `attempts` | ✅ | `test_jobs_queue.py` |
| Bounded retry (attempts exhaust → `dead`, no infinite loop) | ✅ | `test_jobs_queue.py` |
| Lease reclamation of abandoned `running` jobs | ✅ | `test_jobs_queue.py` |
| `PermanentJobError` fails fast without burning retries | ✅ | `test_jobs_queue.py` |
| `graph.build` handler | ✅ | `workers/handlers.py` |
| `document.process` handler | ✅ | `workers/handlers.py`, `test_documents_processing.py` |
| Worker runner (poll loop) | ✅ | `workers/runner.py` |

## 5b. Document ingestion & preprocessing (Checkpoints B & C)

| Feature | Status | Evidence |
| --- | --- | --- |
| Secure upload (magic-byte + real-parser validation) | ✅ | `test_documents_validation.py` |
| Private, immutable, hash-verified `ObjectStore` | ✅ | `test_documents_ingestion.py` |
| Document processing state machine | ✅ | `test_documents_ingestion.py` |
| Idempotent `document.process` enqueue on upload | ✅ | `test_documents_ingestion.py` |
| Upload → queued → worker claims → preprocesses | ✅ | `test_documents_processing.py` |
| Image normalization (EXIF, downscale, grayscale, blank flag) | ✅ | `test_documents_preprocess.py` |
| Coordinate mapping (working ↔ original space) | ✅ | `test_documents_preprocess.py` |
| Page records persisted idempotently (no duplicates on redelivery) | ✅ | `test_documents_processing.py` |
| PDF rasterization via pypdfium2 (optional `pdf` extra) | ✅ | `test_documents_preprocess.py` |
| PDF unavailable-backend → explicit permanent error | ✅ | `test_documents_preprocess.py` |
| Page-count / dimension / pixel / timeout limits enforced | ✅ | `test_documents_preprocess.py` |
| Retryable vs. permanent failure classification | ✅ | `test_documents_processing.py` |
| Lease lost → stale worker cannot finalize | ✅ | `test_documents_processing.py` |
| Cancellation before / during multi-page processing | ✅ | `test_documents_processing.py` |
| Deletion-pending document is never resurrected | ✅ | `test_documents_processing.py` |
| Partial page failure never reaches `preprocessed` | ✅ | `test_documents_processing.py` |
| Safe processing-status API (state, progress, retry, artifacts-ready) | ✅ | `test_documents_ingestion.py` |
| Preprocessing produces **page images only** — no OCR | ✅ | pipeline is not OCR by design |

## 5c. Handwriting OCR (Checkpoint D)

**Scope in one line:** line-level handwriting recognition with traceable
provenance. TrOCR reads *individual text lines*; it does **not** detect question
numbers, associate text with questions, read tables/diagrams, reconstruct
multi-column layout, understand math reliably, or grade anything.

| Feature | Status | Evidence |
| --- | --- | --- |
| Provider-neutral OCR contract (`OcrProvider`) | ✅ | `ocr/base.py`, `test_documents_ocr_providers.py` |
| `none` provider fails explicitly (never invents text) | ✅ | `ocr/none.py`, `test_documents_ocr_providers.py` |
| `fake` provider isolated to tests, refused in production | ✅ | `ocr/registry.py`, `test_documents_ocr_providers.py` |
| TrOCR adapter, real local inference (CPU) | ⚠️ provisional | `ocr/trocr.py`; genuine inference depends on the optional `ocr` extra + cached model |
| Lazy model loading (no torch/transformers at import or startup) | ✅ | verified: `torch`/`transformers` absent from `sys.modules` after import |
| Text-line extraction (projection profile, Pillow-only) | ✅ | `ocr/lines.py`, `test_documents_ocr_providers.py` |
| Blank page → no invented geometry | ✅ | `ocr/lines.py` returns `[]` |
| Coordinate mapping working → original, clamped to page | ✅ | `ocr/lines.py`, `test_documents_ocr_providers.py` |
| Explicit, versioned text normalization (signs/units kept) | ✅ | `ocr/normalization.py` |
| No invented confidence (TrOCR reports `NULL`) | ✅ | `test_documents_ocr_providers.py`, `test_documents_ocr_pipeline.py` |
| Durable `document.ocr` job, separate from preprocessing | ✅ | `ocr_pipeline.py`, `test_documents_ocr_pipeline.py` |
| Region persistence idempotent (`region_uid` unique per page) | ✅ | migration `b7d1e0c4a9f2`, `test_documents_ocr_pipeline.py` |
| Reviewer corrections survive OCR retries | ✅ | `test_documents_ocr_pipeline.py` |
| Lease check — stale worker cannot finalize | ✅ | `test_documents_ocr_pipeline.py` |
| Cancellation / deletion-pending honored | ✅ | `test_documents_ocr_pipeline.py` |
| Partial OCR → `review_required`, never `completed` | ✅ | `test_documents_ocr_pipeline.py` |
| Authorized `GET /documents/{id}/regions` (owner-scoped) | ✅ | `test_documents_ocr_pipeline.py` |
| CER / WER + reproducible JSON evaluation reports | ✅ | `services/evaluation/`, `test_documents_ocr_providers.py` |
| Dataset-license required before real-model scoring | ✅ | `scripts/evaluate_ocr.py` exits 2 without it |
| Real-model smoke test (opt-in, no auto-download) | ⚠️ opt-in | `test_documents_ocr_smoke.py`; skipped in ordinary CI |

## 5d. Question/answer segmentation (Checkpoint E)

**Scope in one line:** deterministic, auditable conversion of recognized OCR
regions into *reviewable question/answer candidates*. Segmentation **infers
structure only** — it never alters OCR evidence, never reports a numeric
confidence, and never decides whether an answer is correct (assessment stays
out of scope). It runs on a status axis of its own, so it can be requested,
fail, and be re-run without touching the OCR `processing_state` lifecycle.

| Feature | Status | Evidence |
| --- | --- | --- |
| Pure rule baseline (`rule_baseline`), no model / LLM / network | ✅ | `segmentation/segmenter.py`, `test_documents_segmentation.py` S01–S15 |
| Versioned rules; version surfaced per segment (`algorithm_version`) | ✅ | `segmentation/rules.py` (`SEGMENT_ALGORITHM_VERSION`) |
| Question / subpart / answer structure with parent–child links | ✅ | S01, S03 |
| Cross-page continuation only when text is unfinished | ✅ | S04 merged → `suspected_continuation`; S05 declined when sentence-final |
| Page furniture (header / footer / page number) excluded from body | ✅ | S06 |
| Positional answers flagged `ambiguous_boundary`, never "verified" | ✅ | S07 |
| Conflicting question numbering flagged `conflicting_numbering` | ✅ | S08 |
| Categorical uncertainty only — no invented numeric confidence | ✅ | `SegmentUncertainty` enum; no score column exists |
| Explicit `unclassified` fallback; no region ever silently dropped | ✅ | S09, S15 |
| Missing OCR text reported as `missing_ocr`, never synthesized | ✅ | S10 |
| Diagram / equation / table detection is conservative | ✅ | S11 (prose *mentioning* them stays prose) |
| Idempotent `segment_uid` (region + type, text/version independent) | ✅ | S12, S17 |
| Segment box is an anchor on `start_page` only; page-local region geometry kept | ✅ | S13, S16 |
| Deterministic output for identical input | ✅ | S14 |
| Durable `document.segment` job, separate from OCR | ✅ | `segmentation_pipeline.py`, S16 |
| Redelivery / explicit reprocess never duplicates segments | ✅ | S17 |
| Lease lost → stale worker does not finalize | ✅ | S18 |
| Reviewer confirmation/correction survives reprocess, regions not reassigned | ✅ | S19 |
| No OCR regions → permanent failure (job dead), bounded safe error | ✅ | S20 |
| Deletion-pending document neither segmented nor resurrected | ✅ | S21 |
| Owner-scoped, audited API (`segment`, `segmentation`, `segments`, review) | ✅ | S22 |
| `segment_regions.region_id` unique → one region, at most one segment | ✅ | migration `d2f6b81c4e07`, S15/S19 |
| Segmentation accuracy on real documents | ❌ not claimed | rules are heuristics; no labeled benchmark exists |

**How to run:** `python -m pytest backend/tests/test_documents_segmentation.py -q`
(22 scenarios, all synthetic fixtures — no model, download, or network).

**Known limits:** rules are English/marker heuristics over OCR output; they do
not understand layout (columns, text boxes), do not parse tables without pipe
delimiters, do not parse math beyond a keyword heuristic, and treat any
unplaceable content as `unclassified`. Every heuristic and its uncertainty
value is defined in `segmentation/rules.py` and `models/documents.py`.

## 5e. Integration audit (Checkpoint F)

**Scope in one line:** no new capability — an end-to-end audit of whether
Checkpoints B–E actually connect, a real integration test over the live stack,
and two verified cross-tenant security defects fixed.

### End-to-end transitions verified

Driven in one continuous scenario through the **real** authenticated API, real
private object store, real SQLite session, real durable queue and the registered
`document.process` / `document.ocr` / `document.segment` handlers
(`backend/tests/test_documents_workflow_e2e.py`):

upload → private storage → preprocess → OCR regions with provenance →
segmentation request → segment structure → retrieval → review correction →
explicit reprocess → job redelivery.

Verified at every step: reviewer corrections survive both a reprocess and a
redelivery; segment identity (`segment_uid`) is stable; no duplicate segments or
region links are ever created; and `processing_state` and `segmentation_status`
remain genuinely independent axes.

### Verified gaps found by the audit

| # | Gap | Priority | Evidence |
| --- | --- | --- | --- |
| G-A | **Document deletion is entirely unimplemented.** No `DELETE` route, no service function, no job handler ever sets `deletion_pending`/`deleted`, and `ObjectStore.delete()` has no production caller. The lifecycle states and skip-guards exist and are correct, but nothing can reach them. | P1 | `api/v1/documents.py` (9 routes, none delete); `git grep` at HEAD shows only `orgs.py` has a DELETE route; `store.delete` called only from tests |
| G-B | **OCR cannot be triggered through the application.** `ingestion.enqueue_ocr` is correct and well tested but has zero production callers — no route, no job chain, no script. Through the real API a document preprocesses and then stops; `POST /segment` returns 409 for want of regions. | P1 | `ingestion.py:233`; grep shows callers only in `test_documents_ocr_pipeline.py` |
| G-C | `enqueue_processing` and `enqueue_ocr` use a fixed document-scoped idempotency key, so once their job succeeds a re-enqueue returns the finished job and schedules nothing. Only `enqueue_segmentation` is run-suffixed and supports explicit reprocessing. Latent today (neither is reachable twice). | P2 | `ingestion.py:226`, `:274` vs `:344`; `jobs.enqueue` returns the existing job regardless of status |
| G-D | `claim_next` uses `.with_for_update()`, which is a no-op on SQLite — concurrent SQLite workers could double-claim. Correct on PostgreSQL, which is unvalidated. | P2 | `services/jobs.py:93` |

### Cross-tenant defects fixed in this checkpoint

Both were reproduced against the running app *before* the fix and confirmed
closed afterwards; regression tests now guard them permanently in
`backend/tests/test_cross_tenant_authorization.py` (9 scenarios).

| Defect | Before | After |
| --- | --- | --- |
| `GET /api/v1/topics` returned **every user's personal topics** — `list_topics` filtered only `org_id IS NULL`, and `create_topic` hardcodes `org_id=None`, so that predicate matched every personal topic in the database | User B's listing contained user A's private topic (status 200, topic id present) | Listing is scoped by the same rule that guards single-topic reads: personal topics visible only to their creator, org topics only to members |
| `GET /api/v1/questions/{id}` had **no object-level authorization** — a bare `session.get` returned another tenant's question *including `reference_answer`*, the grading key. `/questions/{id}/rubrics` (GET+POST) and `POST /assessments` shared the gap | User B read user A's answer key with status 200 | Questions resolve through their topic via a single shared `graph_service.authorize_topic_access`; a non-owner now gets 403/404 and never sees the key |

**How to run:** `python -m pytest backend/tests/test_documents_workflow_e2e.py backend/tests/test_cross_tenant_authorization.py -q`

## 6. Assessment & grading (RubricLens)

| Feature | Status | Evidence |
| --- | --- | --- |
| Question authoring (typed) | ✅ | learning API |
| Rubric authoring (criteria + max points) | ✅ | learning API |
| Deterministic check: numeric | ✅ | `test_grading.py` |
| Deterministic check: exact (case-insensitive) | ✅ | `test_grading.py` |
| Deterministic check: contains all/any | ✅ | `test_grading.py` |
| Deterministic check: regex | ✅ | `test_grading.py` |
| Deterministic check: code (run + stdout compare) | ✅ | `test_grading.py` |
| Deterministic check: prose evidence (requires review) | ✅ | `test_grading.py` |
| Rubric-based scoring with per-criterion results | ✅ | `test_grading.py` |
| Code sandbox | ⚠️ baseline | Subprocess isolation, time limit, dangerous-import rejection; **not** a hardened multi-tenant sandbox |

## 7. Mastery & diagnosis (LearnTrace)

| Feature | Status | Evidence |
| --- | --- | --- |
| Bayesian Knowledge Tracing mastery update | ⚠️ provisional | `test_mastery.py`; **uncalibrated** (`provisional-v1`) |
| Mastery state thresholds (insufficient/learning/demonstrated) | ✅ | `test_mastery.py` |
| Prerequisite-gap diagnosis (competing hypotheses) | ✅ | `test_diagnosis.py` |
| Information-gain question selection | ✅ | `test_diagnosis.py` |
| `insufficient_evidence` when graph/question missing | ✅ | `test_diagnosis.py` |
| Diagnosis does not invent missing prerequisites | ✅ | `test_diagnosis.py` |

## 8. Retention & portfolio

| Feature | Status | Evidence |
| --- | --- | --- |
| SM-2-style spaced schedule | ✅ | `test_retention.py` |
| Review recording (recalled / lapse) | ✅ | `test_retention.py` |
| Due-review queries | ✅ | `test_retention.py` |
| Profile + portfolio curation | ✅ | `test_portfolio.py` |
| Public read-only portfolio endpoint | ✅ | `test_portfolio.py` |

## 9. Platform

| Feature | Status | Evidence |
| --- | --- | --- |
| Health endpoint | ✅ | health API |
| Structured JSON logging + correlation IDs | ✅ | `logging.py` |
| Audit events with redaction | ✅ | `services/audit.py` |
| Alembic migrations (5 chained revisions, single head, 33 tables) | ✅ | verified vs SQLite via `scripts/verify_migration.py`; upgrade → downgrade → re-upgrade all clean |
| CI: lint (flake8), import order (isort), types (mypy), migration verify, tests | ✅ | `.github/workflows/ci.yml` |
| PostgreSQL support | ⚠️ untested here | ORM is dialect-agnostic; only SQLite exercised in this environment |

---

## Not implemented (by design, fail-loud)

- **LLM integration.** `LLM_PROVIDER=none` is the default. The extraction
  boundary raises `UnconfiguredLLMExtractor` rather than silently degrading, so
  no LLM-generated content can appear without an explicit, configured provider.
- **Handwriting OCR (Checkpoint D) — implemented, with stated limits.**
  A real, provider-neutral OCR stage now runs: line detection → TrOCR line
  recognition → traceable region persistence. The default `OCR_PROVIDER=none`
  still fails OCR explicitly rather than inventing text. Limits that are *not*
  claimed: this is **line-level** recognition only. Page-layout understanding,
  question-number detection, table/diagram reading, multi-column
  reconstruction, and reliable math notation are **not implemented**. No
  accuracy figure has been measured against a lawful, representative benchmark,
  so no accuracy or competitor-parity claim is made. Training-data terms for
  TrOCR's IAM fine-tuning corpus were **not verified** in this checkpoint
  (the IAM host was unreachable) — confirm them before commercial deployment.
- **Equation recognition, answer reconstruction, grading-of-handwriting.**
  All belong to later checkpoints; not started. Checkpoint E does *detect* a
  formula-shaped line as an `equation` segment type, but it does **not**
  recognize, parse, LaTeX-ize, or solve it — that is a separate capability.
- **Segmentation is structure, not understanding (Checkpoint E).** The
  question/answer segmentation delivered in E is a deterministic rule baseline
  over OCR regions. It reports *where* questions, subparts, and candidate
  answers appear and how confident the boundary evidence is, in categorical
  terms. It does **not** judge whether any answer is correct — assessment is a
  separate, out-of-scope stage. No precision/recall or accuracy figure is
  claimed for the rules, because no labeled benchmark exists and the TrOCR
  weights still cannot be downloaded in this environment.
- **OCR in the base install.** The default install does not ship torch or
  transformers. Install the optional `ocr` extra (`torch`, `transformers`) to
  enable real recognition. Without it, `OCR_PROVIDER=trocr` fails with a clear,
  permanent `ocr_unavailable` error rather than a wrong or empty result.
- **PDF rasterization in the base install.** The default install does not ship a
  PDF renderer. Install the optional `pdf` extra (`pypdfium2`, BSD-3-Clause /
  Apache-2.0) to enable it. Without it, PDFs that require rasterization fail with
  a clear, permanent `pdf_renderer_unavailable` error rather than a wrong result.
- **Frontend.** A React SPA now covers the primary screens (see `frontend/`),
  but it has **no test suite** — it is type-checked and built only.
- **CI security scanning (SAST / dependency scanning).** A CI pipeline runs
  lint/type/test/migration checks, but no SAST or dependency-vulnerability
  scanning is configured.
- **Hardened code sandbox.** The sandbox is a subprocess baseline with time and
  import controls. It is **not** reviewed as a multi-tenant isolation boundary.
- **Calibrated mastery.** BKT parameters are fixed defaults, explicitly marked
  `provisional-v1`. They are not calibrated to any population.
- **Email delivery / S3 storage.** Local storage backend only; no transactional
  email is wired up.
