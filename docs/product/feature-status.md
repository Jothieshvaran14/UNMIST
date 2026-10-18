# Feature Status

Honest, per-feature status. "Working" means there is code **and** passing tests
that exercise it. Nothing is listed as working without a test.

Legend:

- ✅ **Working** — implemented and covered by passing tests.
- ⚠️ **Provisional / baseline** — implemented and tested, but with a documented
  limitation (uncalibrated model, non-hardened isolation, etc.).
- ❌ **Not implemented** — the boundary exists and fails loudly; no functionality
  is claimed.

Last verified: full backend suite **69 passed** on Python 3.10 / SQLite.

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
| Claim / lease / heartbeat | ✅ | `services/jobs.py` |
| Retry with backoff + max attempts | ✅ | `services/jobs.py` |
| `graph.build` handler | ✅ | `workers/handlers.py` |
| Worker runner (poll loop) | ✅ | `workers/runner.py` |

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
| Alembic migrations (initial schema, 27 tables) | ✅ | verified vs SQLite via `scripts/verify_migration.py` |
| CI: lint (flake8), import order (isort), types (mypy), migration verify, tests | ✅ | `.github/workflows/ci.yml` |
| PostgreSQL support | ⚠️ untested here | ORM is dialect-agnostic; only SQLite exercised in this environment |

---

## Not implemented (by design, fail-loud)

- **LLM integration.** `LLM_PROVIDER=none` is the default. The extraction
  boundary raises `UnconfiguredLLMExtractor` rather than silently degrading, so
  no LLM-generated content can appear without an explicit, configured provider.
- **OCR / handwriting pipeline.** No OCR or handwriting recognition is
  implemented. Do not claim otherwise.
- **Frontend.** Backend only.
- **CI security scanning (SAST / dependency scanning).** A CI pipeline runs
  lint/type/test/migration checks, but no SAST or dependency-vulnerability
  scanning is configured.
- **Hardened code sandbox.** The sandbox is a subprocess baseline with time and
  import controls. It is **not** reviewed as a multi-tenant isolation boundary.
- **Calibrated mastery.** BKT parameters are fixed defaults, explicitly marked
  `provisional-v1`. They are not calibrated to any population.
- **Email delivery / S3 storage.** Local storage backend only; no transactional
  email is wired up.
