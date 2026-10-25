# Requirements Traceability

Maps each product requirement to the code that implements it and the test that
verifies it. Requirements with no implementation are marked **Not implemented**
rather than claimed.

## Legend

- **R#** — requirement identifier.
- **Implemented by** — primary module(s).
- **Verified by** — test module / test name.
- **Status** — Working / Provisional / Not implemented.

---

## A. Accounts, auth, tenancy

| ID | Requirement | Implemented by | Verified by | Status |
| --- | --- | --- | --- | --- |
| R-A1 | Users can register with email + password | `api/v1/auth.py`, `services/auth.py` | `test_auth.py` | Working |
| R-A2 | Passwords stored with a memory-hard KDF | `security/passwords.py` (Argon2id) | `test_auth.py` | Working |
| R-A3 | Short-lived access token + rotating refresh token | `security/tokens.py`, `services/auth.py` | `test_auth.py` | Working |
| R-A4 | Refresh token reuse revokes the family | `services/auth.py` | `test_auth.py` | Working |
| R-A5 | Organizations with roles | `models/org.py`, `security/rbac.py` | orgs API tests | Working |
| R-A6 | Every org-scoped route enforces authorization server-side | `security/dependencies.py` (`OrgRole`) | orgs API tests | Working |

## B. Lawful ingestion & provenance

| ID | Requirement | Implemented by | Verified by | Status |
| --- | --- | --- | --- | --- |
| R-B1 | Sources carry explicit license metadata | `models/knowledge.py` (`Source`), `services/knowledge/sources.py` | sources tests | Working |
| R-B2 | Ingestion is blocked when the license does not permit it | `services/knowledge/sources.py`, `ingestion.py` | license enforcement tests | Working |
| R-B3 | Retrieved content is traceable to a document + passage | `models/knowledge.py` (`SourceDocument`, `SourcePassage`) | `test_extraction.py` | Working |
| R-B4 | Outbound retrieval is SSRF-safe | `services/knowledge/retrieval.py` | `test_retrieval.py` | Working |
| R-B5 | Outbound retrieval respects robots.txt | `services/knowledge/retrieval.py` | `test_retrieval.py` | Working |
| R-B6 | Retrieval enforces size / redirect / time limits | `services/knowledge/retrieval.py` | `test_retrieval.py` | Working |
| R-B7 | Retrieval allowlist is deny-by-default (empty = no egress) | `config.py`, `retrieval.py` | `test_retrieval.py` | Working |

## C. Extraction & claims (no fabrication)

| ID | Requirement | Implemented by | Verified by | Status |
| --- | --- | --- | --- | --- |
| R-C1 | Deterministic extraction baseline without an LLM | `services/knowledge/extraction.py`, `concept_extraction.py` | `test_extraction.py` | Working |
| R-C2 | Claims cite a source passage | `models/knowledge.py` (`Claim`), `extraction.py` | `test_extraction.py` | Working |
| R-C3 | Missing evidence yields `insufficient_evidence`, never a guess | `validation.py`, `diagnosis.py`, `grading.py` | `test_validation.py`, `test_diagnosis.py` | Working |
| R-C4 | Unconfigured LLM fails loudly rather than degrading silently | `concept_extraction.py` (`UnconfiguredLLMExtractor`) | extraction tests | Working |
| R-C5 | LLM content is labeled as model-generated, separate from verified graph | `concept_extraction.py`, `diagnosis.py` | design + code | Working |

## D. Knowledge graph

| ID | Requirement | Implemented by | Verified by | Status |
| --- | --- | --- | --- | --- |
| R-D1 | Concepts have stable keys and canonical names | `models/knowledge.py` (`Concept`), `graph_service.py` | `test_graph_service.py` | Working |
| R-D2 | Relationships carry type + evidence + confidence | `models/knowledge.py` (`ConceptRelationship`) | `test_graph_service.py` | Working |
| R-D3 | Graph builds are validated (cycles, dupes, evidence, orphans) | `services/knowledge/validation.py` | `test_validation.py` | Working |
| R-D4 | Published graph versions are immutable | `models/knowledge.py` (`GraphVersion`), `graph_service.py` | `test_graph_service.py` | Working |
| R-D5 | Rollback to a prior published version | `graph_service.py` | `test_graph_service.py` | Working |

## E. Jobs

| ID | Requirement | Implemented by | Verified by | Status |
| --- | --- | --- | --- | --- |
| R-E1 | Long-running work runs in a durable queue, not in-request | `services/jobs.py`, `workers/runner.py` | `test_jobs_queue.py` | Working |
| R-E2 | Enqueue is idempotent | `services/jobs.py` | `test_jobs_queue.py` | Working |
| R-E3 | Claiming sets `running`/`started_at` and increments `attempts` | `services/jobs.py` (`claim_next`) | `test_jobs_queue.py` | Working |
| R-E4 | Failures retry with backoff, bounded by max attempts (no infinite loop) | `services/jobs.py` (`mark_failed`) | `test_jobs_queue.py` | Working |
| R-E5 | Abandoned `running` jobs are reclaimed after lease expiry | `services/jobs.py` (`reclaim_expired`) | `test_jobs_queue.py` | Working |
| R-E6 | Permanent input errors fail fast without burning the retry budget | `services/jobs.py` (`PermanentJobError`) | `test_jobs_queue.py` | Working |

## E2. Document ingestion & preprocessing (Checkpoints B & C)

| ID | Requirement | Implemented by | Verified by | Status |
| --- | --- | --- | --- | --- |
| R-D1 | Uploads are validated by magic bytes + a real parser, never by client MIME/filename | `services/documents/validation.py` | `test_documents_validation.py` | Working |
| R-D2 | Originals are stored privately, immutably, with hash verification | `services/storage.py` (`LocalObjectStore`) | `test_documents_ingestion.py` | Working |
| R-D3 | Document lifecycle follows an explicit, validated state machine | `services/documents/state.py` | `test_documents_ingestion.py` | Working |
| R-D4 | Upload schedules an idempotent `document.process` job | `services/documents/ingestion.py` | `test_documents_ingestion.py` | Working |
| R-D5 | A worker claims the job, verifies integrity, and preprocesses | `services/documents/pipeline.py` | `test_documents_processing.py` | Working |
| R-D6 | Image normalization: EXIF orientation, downscale-only, grayscale, blank flag | `services/documents/preprocess.py` | `test_documents_preprocess.py` | Working |
| R-D7 | Original↔working coordinate mapping is preserved and auditable | `preprocess.py`, `DocumentPage.working_scale` | `test_documents_preprocess.py` | Working |
| R-D8 | Page records are persisted idempotently (no duplicates on redelivery) | `pipeline.py`, `generate_page_key` | `test_documents_processing.py` | Working |
| R-D9 | PDF rasterization uses a verified, permissively-licensed backend | `preprocess.py` (pypdfium2, `pdf` extra) | `test_documents_preprocess.py` | Working (opt-in) |
| R-D10 | Missing renderer yields an explicit permanent error, not a wrong result | `preprocess.py` | `test_documents_preprocess.py` | Working |
| R-D11 | Page-count / dimension / pixel / per-page / job timeouts are enforced | `preprocess.py`, `config.py` | `test_documents_preprocess.py` | Working |
| R-D12 | Retryable vs. permanent failures are distinguished | `pipeline.py`, `jobs.py` | `test_documents_processing.py` | Working |
| R-D13 | A stale worker whose lease expired cannot finalize state | `pipeline.py` (`_still_owns_lease`) | `test_documents_processing.py` | Working |
| R-D14 | Cancellation is honored before and during multi-page processing | `pipeline.py` | `test_documents_processing.py` | Working |
| R-D15 | A deletion-pending document is never resurrected by a worker | `pipeline.py` (`_SKIP_STATES`) | `test_documents_processing.py` | Working |
| R-D16 | Partial page failure never reaches `preprocessed`/`completed` | `state.py` (`mark_preprocessed`) | `test_documents_processing.py` | Working |
| R-D17 | Processing status is exposed safely (no keys, paths, or stack traces) | `api/v1/documents.py`, `schemas/documents.py` | `test_documents_ingestion.py` | Working |
| R-D18 | Preprocessing produces page images only; no OCR is performed or claimed | `pipeline.py` by design | `test_documents_processing.py` | Working |

## E3. Handwriting OCR (Checkpoint D)

**Scope:** line-level handwriting recognition with traceable provenance. TrOCR reads *individual text lines*; it does **not** detect question numbers, associate text with questions, read tables/diagrams, reconstruct multi-column layout, understand math reliably, or grade anything.

| ID | Requirement | Implemented by | Verified by | Status |
| --- | --- | --- | --- | --- |
| R-D19 | Provider-neutral OCR contract; providers fail explicitly rather than fabricate text | `ocr/base.py`, `ocr/none.py` | `test_documents_ocr_providers.py` | Working |
| R-D20 | Real TrOCR adapter, lazy model load (no torch/transformers at import or startup) | `ocr/trocr.py` | `test_documents_ocr_providers.py`, `test_documents_ocr_smoke.py` | Provisional (opt-in) |
| R-D21 | Text-line extraction (projection profile, Pillow-only); blank page yields no invented geometry | `ocr/lines.py` | `test_documents_ocr_providers.py` | Working |
| R-D22 | Coordinates mapped back to original image space and clamped to page bounds | `ocr/lines.py` (`map_box_to_original`) | `test_documents_ocr_providers.py` | Working |
| R-D23 | Explicit, versioned text normalization that never strips signs/decimals/units/punctuation | `ocr/normalization.py` | `test_documents_ocr_providers.py` | Working |
| R-D24 | No invented confidence (TrOCR reports `NULL`) | `ocr/trocr.py` | `test_documents_ocr_providers.py`, `test_documents_ocr_pipeline.py` | Working |
| R-D25 | Durable `document.ocr` job separate from preprocessing, with lease + cancellation + idempotent region persistence | `ocr_pipeline.py`, `workers/handlers.py` | `test_documents_ocr_pipeline.py` | Working |
| R-D26 | Reviewer corrections survive OCR retries; partial OCR → `review_required`, never `completed` | `ocr_pipeline.py`, `state.py` (`mark_completed`) | `test_documents_ocr_pipeline.py` | Working |
| R-D27 | Authorized, owner-scoped OCR-region access (no keys, paths, or stack traces) | `api/v1/documents.py`, `schemas/documents.py` | `test_documents_ocr_pipeline.py` | Working |
| R-D28 | Reproducible CER/WER evaluation with recorded provenance; real-model scoring requires stated dataset license | `services/evaluation/`, `scripts/evaluate_ocr.py` | `test_documents_ocr_providers.py` | Working |

## E4. Question-and-answer segmentation (Checkpoint E)

**Scope:** turn persisted OCR regions into *reviewable* question/answer structure with evidence, geometry, ordering, and categorical uncertainty preserved. Segmentation is **inference about structure**, never a judgement about correctness (assessment stays out of scope). It runs on its own lifecycle axis (`segmentation_status`), so it never mutates `processing_state`. The baseline is pure deterministic rules — no LLM, no model download, no numeric confidence.

> **Unresolved blocker carried from Checkpoint D:** genuine TrOCR inference still cannot execute in this environment (the ~1.27 GB `model.safetensors` download is egress-blocked). Segmentation is therefore verified only against synthetic and fixture-supplied regions; its behaviour on real handwriting output remains untested end to end.

| ID | Requirement | Implemented by | Verified by | Status |
| --- | --- | --- | --- | --- |
| R-E33 | Pure deterministic baseline segmenter; same input yields identical output; no LLM required (`LLM_PROVIDER=none` still default) | `segmentation/segmenter.py`, `segmentation/rules.py` | `test_documents_segmentation.py` S01–S15 | Working |
| R-E34 | Versioned rules in one place; algorithm + version recorded on every segment | `rules.py` (`SEGMENT_ALGORITHM_VERSION`), `derive_segment_uid` | S01, S16, S19 | Working |
| R-E35 | Question / subpart / answer structure with explicit parent–child links | `segmenter.py` (`_walk_body`, `_apply_parents`) | S01, S03, S16 | Working |
| R-E36 | Conservative cross-page continuation: merged only when text is unfinished, flagged `suspected_continuation`; declines recorded | `segmenter.py` (`_can_continue_across_pages`, `declined_continuation`) | S04, S05 | Working |
| R-E37 | Page-local geometry never globally compared; anchor box on `start_page` only; per-region geometry kept on `OcrRegion` via links | `segmenter.py`, `segment_regions`, migration `d2f6b81c4e07` | S13, S16 | Working |
| R-E38 | Categorical uncertainty only; semantics documented per value; no numeric confidence column exists | `models/documents.py` (`SegmentUncertainty`) | S07, S10, S11 | Working |
| R-E39 | No silent drops: unplaceable content becomes explicit `unclassified`; missing OCR text becomes `missing_ocr` | `segmenter.py` (`_build_candidates`, `_normalize`) | S09, S10, S15 | Working |
| R-E40 | Observed vs inferred vs reviewer-confirmed kept separate; a positional answer is `ambiguous_boundary`, never "verified" | `segmenter.py`, `models/documents.py` (`provenance`, `review_status`) | S07 | Working |
| R-E41 | Idempotent segment identity independent of text/version; one region maps to at most one segment | `derive_segment_uid`, `UNIQUE(segment_regions.region_id)` | S12, S15, S17, S19 | Working |
| R-E42 | Durable `document.segment` job: lease, cancellation, chunked commits, permanent vs retryable classification; OCR pipeline semantics intact | `segmentation_pipeline.py`, `workers/handlers.py` | S16, S18, S20, S21 | Working |
| R-E43 | Partial outcomes never reported as success (lease loss / mid-run deletion stays `processing`/`cancelled`) | `segmentation_pipeline.py` (`_persist`, `_mark_cancelled`) | S18, S21 | Working |
| R-E44 | Reviewer corrections survive reprocessing; reviewed rows never overwritten/pruned; regions never reassigned | `segmentation_pipeline.py` (`_prune_stale`, `_preserved_region_ids`) | S19 | Working |
| R-E45 | Owner-scoped, validated, audited API; cross-tenant access returns 404 (no id probing); safe errors | `api/v1/documents.py`, `schemas/documents.py`, `services/audit.py` | S22 | Working |
| R-E46 | Segmentation accuracy on real documents | — | — | **Not claimed** — heuristics over OCR output; no labeled benchmark exists |

## E5. Integration audit & end-to-end validation (Checkpoint F)

**Scope:** no new capability — establish whether Checkpoints B–E actually connect.
One continuous end-to-end scenario over the real stack, a full workflow trace,
a security/lifecycle boundary review, migration re-validation, and a
production-readiness gap report. Two verified cross-tenant defects were fixed.

| ID | Requirement | Implemented by | Verified by | Status |
| --- | --- | --- | --- | --- |
| R-F1 | End-to-end scenario drives the **real** stack (auth API, real store, real queue, registered handlers) — not a test-only reimplementation | — | `test_documents_workflow_e2e.py` | Working |
| R-F2 | Upload → private storage → preprocess → OCR regions (provenance, page-local geometry) → segmentation → retrieval → review → reprocess → redelivery verified in one scenario | pipeline modules (C/D/E) | same | Working |
| R-F3 | OCR `processing_state` and `segmentation_status` remain genuinely independent axes at every stage | `models/documents.py`, both pipelines | same | Working |
| R-F4 | Reviewer corrections survive both an explicit reprocess and a job redelivery; segment identity stable; no duplicate segments or region links | `segmentation_pipeline.py` | same | Working |
| R-F5 | End-to-end tenant isolation: a second tenant gets 404 (no id disclosure) at every document read/write; list endpoint omits it | `api/v1/documents.py` | same | Working |
| R-F6 | Org scoping enforced server-side on ingestion (member 201 with `org_id`; non-member refused) | `api/v1/documents.py` (`_authorize_org_write`) | same | Working |
| R-F7 | Migration chain re-validated: fresh upgrade, stepwise downgrade, re-upgrade, model-vs-migration match, dedupe constraints confirmed in the migrated schema | `migrations/versions/*`, `verify_migration.py`, `verify_region_uid.py` | Checkpoint F run (see `testing.md`) | Working (SQLite only) |
| R-F8 | `GET /api/v1/topics` scoped to the caller's own personal topics + orgs they belong to | `graph_service.list_topics` (`viewer_id`), `authorize_topic_access` | `test_cross_tenant_authorization.py` (3 scenarios) | Working (fixed in F) |
| R-F9 | Question/assessment routes enforce object-level authorization; `reference_answer` never disclosed cross-tenant; questions cannot be attached to a topic the caller cannot write | `api/v1/learning.py` (`_load_authorized_question`, `_authorize_rubric_org`) | same (6 scenarios) | Working (fixed in F) |
| R-F10 | Document deletion and cleanup reachable through the product | — | — | **Not implemented** — no route/service/handler reaches `deletion_pending`/`deleted`; `ObjectStore.delete()` has no production caller |
| R-F11 | OCR triggerable through the application | `ingestion.enqueue_ocr` (service only) | — | **Gap** — correct and tested, but zero production callers; no HTTP/job/script path |
| R-F12 | PostgreSQL validation | — | — | **Not performed** — no server or driver available; nothing provisioned |

## F. Assessment & grading

| ID | Requirement | Implemented by | Verified by | Status |
| --- | --- | --- | --- | --- |
| R-F1 | Questions are typed and versioned | `models/learning.py` (`Question`) | learning API tests | Working |
| R-F2 | Rubrics define criteria with max points | `models/learning.py` (`Rubric`) | `test_grading.py` | Working |
| R-F3 | Deterministic graders: numeric, exact, contains, regex, code, prose | `services/learning/checks.py` | `test_grading.py` | Working |
| R-F4 | Grading produces per-criterion results and a total | `services/learning/grading.py` | `test_grading.py` | Working |
| R-F5 | Prose criteria requiring human review are flagged, not auto-passed | `services/learning/checks.py` | `test_grading.py` | Working |
| R-F6 | Code answers execute in an isolated sandbox | `services/learning/sandbox.py` | `test_grading.py` | Provisional (baseline isolation) |

## G. Mastery & diagnosis

| ID | Requirement | Implemented by | Verified by | Status |
| --- | --- | --- | --- | --- |
| R-G1 | Mastery is estimated from assessment evidence | `services/learning/mastery.py` (BKT) | `test_mastery.py` | Provisional (uncalibrated) |
| R-G2 | Low evidence yields `insufficient_evidence`, not a confident score | `mastery.py` | `test_mastery.py` | Working |
| R-G3 | Diagnosis generates competing hypotheses for a struggle | `services/learning/diagnosis.py` | `test_diagnosis.py` | Working |
| R-G4 | The next diagnostic question is chosen by information gain | `diagnosis.py` | `test_diagnosis.py` | Working |
| R-G5 | Diagnosis never invents missing prerequisites | `diagnosis.py` | `test_diagnosis.py` | Working |

## H. Retention & portfolio

| ID | Requirement | Implemented by | Verified by | Status |
| --- | --- | --- | --- | --- |
| R-H1 | Reviews schedule the next encounter (spaced repetition) | `services/retention/scheduling.py` | `test_retention.py` | Working |
| R-H2 | A lapse resets progress and records the lapse | `scheduling.py` | `test_retention.py` | Working |
| R-H3 | Learners can curate a portfolio of demonstrated work | `services/profiles/portfolio.py` | `test_portfolio.py` | Working |
| R-H4 | Portfolios have a public read-only view | `api/v1/profiles.py` | `test_portfolio.py` | Working |

## I. Platform

| ID | Requirement | Implemented by | Verified by | Status |
| --- | --- | --- | --- | --- |
| R-I1 | Health endpoint for liveness | `api/v1/health.py` | health tests | Working |
| R-I2 | Structured logs with correlation IDs | `logging.py` | logging tests | Working |
| R-I3 | Audit trail for sensitive actions, with redaction | `services/audit.py` | audit tests | Working |
| R-I4 | Schema is managed by migrations, not ad-hoc DDL | `migrations/versions/*` | `scripts/verify_migration.py` | Working |
| R-I5 | Configuration is validated; unsafe values refuse startup | `config.py` | config tests | Working |
| R-I6 | Automated checks run in CI (lint, types, migration verify, tests) | `.github/workflows/ci.yml` | CI workflow | Working |

---

## Not implemented (no requirement is claimed as met)

| Area | Status | Boundary behavior |
| --- | --- | --- |
| LLM extraction/generation | Not implemented | `LLM_PROVIDER=none`; raises rather than degrading |
| OCR / handwriting recognition | Implemented (line-level, Checkpoint D) | `OCR_PROVIDER=none` default fails OCR explicitly; `trocr` needs the opt-in `ocr` extra. Line-level only — no page-layout understanding, question detection, table/diagram reading, or reliable math. No accuracy claim without a lawful benchmark. Training-data (IAM) terms unverified. |
| Equation recognition / answer reconstruction / handwriting grading | Not implemented | Later checkpoints; not started. Checkpoint E *detects* a formula-shaped line as an `equation` segment type but does not parse or solve it. |
| Question/answer segmentation | Implemented (structure-only, Checkpoint E) | Deterministic rules over OCR regions; categorical uncertainty; never alters OCR evidence; **no** accuracy claim — behavior verified on synthetic fixtures only, and no labeled benchmark exists. Does not decide whether an answer is correct (assessment, out of scope). |
| TrOCR model weights in this environment | Blocked | The ~1.27 GB `model.safetensors` download is egress-blocked, so real TrOCR inference and real-region segmentation remain unverified. Run `scripts/download_trocr_model.py` on a host with HuggingFace CDN access, then the opt-in smoke test. |
| OCR in the base install | Opt-in extra | Without `ocr` extra, `OCR_PROVIDER=trocr` fails with a permanent `ocr_unavailable` error |
| Frontend | Implemented (React SPA) | `frontend/`: all primary screens type-check and build. **No frontend test suite** exists. |
| CI security scanning (SAST / dependency scanning) | Not implemented | CI runs lint/type/test/migration only |
| Hardened multi-tenant sandbox | Not implemented | Baseline subprocess isolation only |
| Calibrated mastery model | Not implemented | Fixed defaults, marked `provisional-v1` |
| Email delivery / S3 storage | Not implemented | Local storage backend only |
| PostgreSQL validation | Not implemented here | ORM is dialect-agnostic; only SQLite exercised in this environment |
