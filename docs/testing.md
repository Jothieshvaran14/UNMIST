# Testing

## How to run

```powershell
$env:PYTHONPATH="backend"
python -m pytest backend/tests -q
```

Tests run against **SQLite** (in-memory-per-file) with foreign keys enforced,
so they need no external services. The suite sets its own test environment in
`conftest.py`; you do not need a `.env` file to run tests.

### Opt-in real-model OCR smoke test

Ordinary runs skip the real-model test and never download anything. To exercise
genuine TrOCR inference, first populate the Hugging Face cache once (manual,
never automatic):

```powershell
$env:PYTHONPATH="backend"
python scripts/download_trocr_model.py   # ~1.27 GiB; requires the `ocr` extra
# then opt in:
$env:UNMIST_OCR_SMOKE="1"
$env:OCR_REAL_MODEL_SMOKE_ENABLED="1"
python -m pytest backend/tests/test_documents_ocr_smoke.py -q -s
```

If the model is not cached, the smoke test reports the blocker and skips
honestly rather than substituting the fake provider or downloading silently.

### Model-quality evaluation (separate from the test suite)

`scripts/evaluate_ocr.py` scores a provider against a lawful ground-truth
JSONL file and writes a reproducible CER/WER report. It is never run by the
test suite; real-model scoring is refused unless `--dataset-license` is given.

## Current state

Last verified full run: **262 passed, 1 skipped** (the opt-in real-model OCR
smoke test) on Python 3.10 / SQLite.

Test categories, reported separately:

- **Fast unit** — pure logic, no DB, no model (normalization, geometry, line
  extraction, CER/WER, config validation, question/answer segmentation).
- **Integration (with doubles)** — the real queue, DB, and object store driven
  by the `fake` OCR provider; no model, download, or GPU.
- **Opt-in real-model smoke** — `test_documents_ocr_smoke.py`; skipped unless
  `UNMIST_OCR_SMOKE=1` and `OCR_REAL_MODEL_SMOKE_ENABLED=1`. Ordinary CI never
  loads a model or downloads weights.
- **Model-quality evaluation** — `scripts/evaluate_ocr.py`; a separate,
  explicit tool, never part of the test suite.

| Test module | Focus |
| --- | --- |
| `test_auth.py` | Registration, login, Argon2id, access/refresh tokens, rotation, reuse detection |
| `test_retrieval.py` | SSRF defenses, host allowlist, private-IP blocking, robots.txt, size/redirect limits |
| `test_extraction.py` | Deterministic extraction, claim citation, LLM boundary fails loud |
| `test_validation.py` | Graph validation: cycles, duplicates, evidence, orphans |
| `test_graph_service.py` | Graph build, immutable versions, rollback |
| `test_grading.py` | Deterministic checks (numeric/exact/contains/regex/code/prose), rubric scoring, sandbox |
| `test_mastery.py` | BKT mastery updates and state thresholds |
| `test_diagnosis.py` | Prerequisite-gap hypotheses, question selection, `insufficient_evidence` |
| `test_retention.py` | Spaced schedule, review recording, due queries |
| `test_portfolio.py` | Portfolio curation, public read-only endpoint |
| `test_jobs_queue.py` | Queue claim/lease/attempts, bounded retry, permanent errors, reclamation |
| `test_documents_validation.py` | Magic-byte + real-parser upload validation, limits, filename sanitization |
| `test_documents_ingestion.py` | Upload/storage/state machine, authorization, IDOR, processing-status API |
| `test_documents_preprocess.py` | Image normalization, EXIF, downscale, coordinate mapping, PDF rasterization + limits |
| `test_documents_processing.py` | `document.process` end-to-end: claim, idempotency, lease, retry, cancellation, deletion |
| `test_documents_ocr_providers.py` | OCR contract, none/fake providers, normalization, bbox validation, coordinate mapping, line extraction, provider selection + production guard, CER/WER metrics |
| `test_documents_ocr_pipeline.py` | `document.ocr` end-to-end: persisted regions, idempotent redelivery, reviewer-correction preservation, provider-unavailable, lease expiry, stale-worker, cancellation, deletion, `/regions` endpoint authorization + tenant isolation |
| `test_documents_ocr_smoke.py` | Opt-in real TrOCR inference (skipped in ordinary CI) |
| `test_documents_segmentation.py` | Checkpoint E: 22 enumerated scenarios — pure segmenter (S01–S15), durable `document.segment` job (S16–S21), API auth/validation/audit (S22) |
| `test_documents_workflow_e2e.py` | Checkpoint F: the whole document workflow in one continuous scenario over the live stack (upload → preprocess → OCR → segments → review → reprocess → redelivery), plus end-to-end tenant isolation and org scoping |
| `test_cross_tenant_authorization.py` | Checkpoint F: regression tests for two verified P0 cross-tenant defects (topic listing leak; question/answer-key object authorization) — 9 scenarios |

### Integration scenario (Checkpoint F)

`test_documents_workflow_e2e.py` exercises the **real application stack**, not a
test-only reimplementation: authenticated multipart HTTP upload, the real
private object store, the real SQLite session, the real durable job queue, and
the registered `document.process` / `document.ocr` / `document.segment`
handlers.

Two boundaries are honest and deliberate, and are recorded rather than papered
over:

- **OCR has no API trigger.** `ingestion.enqueue_ocr` is correct and tested but
  has no production caller, so the test calls that service function directly —
  exactly as the Checkpoint D tests do. The upload → preprocess → OCR hop is
  therefore verified at the *service* boundary, not over HTTP.
- **The OCR text is fixture-derived, not recognized.** The genuine `fake`
  provider still runs the real line extractor and the real coordinate mapper, so
  geometry, provenance and persistence are genuinely exercised. Nothing in this
  suite supports any claim about TrOCR accuracy, which stays unverified because
  the weights cannot be downloaded in this environment.

### Segmentation scenarios (Checkpoint E)

`test_documents_segmentation.py` enumerates 22 scenarios, indexed in its module
docstring (S01–S22). S01–S15 drive `BaselineSegmenter` with synthetic
`RegionInput`/`PageInput` fixtures — no database, model, download, or network.
S16–S21 drive the real queue job over SQLite with hand-written `OcrRegion`
rows (Checkpoints B/C/D concerns are already covered elsewhere, so segmentation
consumes persisted evidence directly). S22 exercises the HTTP surface:
authentication, ownership, validation, tenant isolation, and audit.

```powershell
$env:PYTHONPATH="backend"
python -m pytest backend/tests/test_documents_segmentation.py -q
```

The fixtures are deliberately synthetic. **They do not establish segmentation
accuracy on real documents** — there is no labeled benchmark, and the rules are
documented heuristics, not a document-understanding model.

## Test design principles

- **No network in tests.** `test_retrieval.py` injects a fake `httpx` transport
  and a fake DNS resolver, so SSRF and robots behavior are verified without real
  egress. This is why `RetrievalClient` accepts `transport=` and `resolver=`.
- **Real FK constraints.** Tests create real `User`/`Topic` rows rather than
  string literals for foreign keys, so referential integrity is actually
  exercised.
- **Fresh DB per test.** The `db_session` fixture creates and drops tables per
  test, so tests are order-independent and isolated.
- **No fabricated data.** Tests assert honest behavior — including the
  `insufficient_evidence` and fail-loud paths — rather than happy-path-only
  assertions.

## Schema / migration verification

Migrations are checked against the ORM metadata by:

```powershell
$env:PYTHONPATH="backend"
python -m alembic upgrade head   # against a scratch SQLite DB
python scripts/verify_migration.py <db_path>
```

`scripts/verify_migration.py` compares the migrated table set to
`Base.metadata` and fails if any table is missing or extra.

### Checkpoint F re-validation

Re-run against a dedicated scratch database, all clean:

| Check | Result |
| --- | --- |
| Full revision chain | 5 revisions, single head `d2f6b81c4e07` (no branches) |
| Fresh `alembic upgrade head` | 5 upgrades applied, exit 0 |
| Stepwise `alembic downgrade base` | 5 downgrades applied, exit 0 |
| Re-`upgrade head` after downgrade | 5 upgrades re-applied, exit 0 |
| `verify_migration.py` | 33 expected / 34 migrated (incl. `alembic_version`) — **OK: matches model metadata** |
| `verify_region_uid.py` | `region_uid` present, `(page_id, region_uid)` unique — **OK** |

Dedupe and integrity protections confirmed present in the **migrated** schema
(inspected via `PRAGMA`, not just the ORM definitions):

- `document_segments` — `UNIQUE(document_id, segment_uid)`, 2 CHECK, 3 FK
- `segment_regions` — `UNIQUE(region_id)` and `UNIQUE(segment_id, position)`, 1 CHECK, 2 FK
- `segment_reviews` — `UNIQUE(segment_id, revision)`, 1 CHECK, 2 FK
- `ocr_regions` — `UNIQUE(page_id, region_uid)`, 2 CHECK, 2 FK
- `document_pages` — `UNIQUE(document_id, page_number)`, 2 CHECK, 1 FK

**PostgreSQL-specific assumptions that remain unverified** (no server or
`psycopg`/`asyncpg` driver is available in this environment; nothing was
provisioned):

- `server_default=sa.text('(CURRENT_TIMESTAMP)')` renders a SQLite-flavoured
  parenthesised literal; its acceptance by PostgreSQL's DDL has not been run.
- `jobs.claim_next` relies on `.with_for_update()`, a **no-op on SQLite** — so
  the queue's row-level claim safety is only real on PostgreSQL.
- `op.batch_alter_table(...)` is used throughout; on SQLite it recreates tables.
- `sa.JSON()` column types and the `render_as_batch` migration mode in
  `migrations/env.py` are SQLite-oriented.
- Pool settings in `backend/app/db.py` apply only on the PostgreSQL branch and
  have never been exercised.

The exact checks still required: add a CI job with a PostgreSQL service
container and run `alembic upgrade head`, `scripts/verify_migration.py`, the
constraint inspection above, and the full suite against it.

## What is **not** covered (honest gaps)

- **No PostgreSQL run.** The ORM layer is dialect-agnostic, but only SQLite is
  exercised in this environment. A PostgreSQL integration job is not configured.
- **No load / performance tests.**
- **No fuzzing** of the retrieval or extraction parsers.
- **No segmentation accuracy benchmark.** Checkpoint E is verified for
  *behavior* (structure, ordering, coverage, idempotency, reviewer
  preservation) on synthetic fixtures only. Its precision/recall on real
  documents is unmeasured and **not claimed**.
- **Segmentation on real OCR output is untested end to end** — the Checkpoint D
  TrOCR model weights still cannot be downloaded in this environment (see
  [`feature-status.md`](product/feature-status.md)), so no real region set has
  been run through the segmenter.
- **PostgreSQL is unvalidated** for the segmentation tables specifically (as for
  every other table here); the migration and schema checks are SQLite-only.
- **No security scanning** (SAST/dependency) — the CI pipeline runs
  lint/type/test/migration checks only (`.github/workflows/ci.yml`).
- **No frontend tests** — the React SPA in `frontend/` has no unit or
  end-to-end suite; it is type-checked (`npm run typecheck`) and built
  (`npm run build`) only.
- **Sandbox escape testing.** The sandbox is a baseline subprocess; its
  resistance to a determined escape has not been adversarially tested. See
  [`security.md`](security.md).
- **Deletion and cleanup are unverified end to end because they do not exist.**
  The Checkpoint F audit confirmed no production code path ever reaches
  `deletion_pending`/`deleted` and `ObjectStore.delete()` has no production
  caller. The pipeline *skip-guards* for those states are tested (the tests set
  the state directly), but no user-visible deletion workflow is exercised —
  because there is none to exercise.
- **The upload → preprocess → OCR transition is not exercised over HTTP.**
  `ingestion.enqueue_ocr` has no production caller, so the end-to-end test
  reaches OCR at the service boundary. Whether an HTTP trigger would behave
  identically is unverified.
- **No authorization tests existed for the topics/learning surface before this
  checkpoint.** Two P0 cross-tenant defects were found and fixed
  (`test_cross_tenant_authorization.py`), but coverage there is still narrow —
  it targets the specific defects rather than a full role/tenant matrix.
- **Job queue concurrency is untested beyond a single worker.** SQLite's
  `.with_for_update()` is a no-op, so any multi-worker claim race is invisible
  here and would need PostgreSQL to reproduce.

## Continuous integration

`.github/workflows/ci.yml` runs on pushes to `main` and on pull requests:

1. **flake8** — style/lint.
2. **isort** — import order (check-only).
3. **mypy** — static types.
4. **alembic upgrade head** + `scripts/verify_migration.py` — migration applies
   and matches the ORM metadata.
5. **pytest** — the full test suite, against SQLite (no external services).

CI does **not** currently include SAST or dependency-vulnerability scanning.

## Adding tests

1. Put them in `backend/tests/test_<area>.py`.
2. Use the `db_session` / `client` fixtures from `conftest.py`; create real
   parent rows for any foreign key.
3. Keep them hermetic: no network, no wall-clock sleeps beyond what is needed,
   no reliance on test ordering.
4. Assert honest behavior — if a feature is provisional, assert the provisional
   contract (e.g. `insufficient_evidence`) rather than a confident result.
