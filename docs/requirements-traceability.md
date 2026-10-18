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
| R-E1 | Long-running work runs in a durable queue, not in-request | `services/jobs.py`, `workers/runner.py` | jobs tests | Working |
| R-E2 | Enqueue is idempotent | `services/jobs.py` | jobs tests | Working |
| R-E3 | Jobs are claimed with a lease; crashed workers release work | `services/jobs.py` | jobs tests | Working |
| R-E4 | Failures retry with backoff up to a max attempt | `services/jobs.py` | jobs tests | Working |

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
| OCR / handwriting | Not implemented | — |
| Frontend | Not implemented | — |
| CI security scanning (SAST / dependency scanning) | Not implemented | CI runs lint/type/test/migration only |
| Hardened multi-tenant sandbox | Not implemented | Baseline subprocess isolation only |
| Calibrated mastery model | Not implemented | Fixed defaults, marked `provisional-v1` |
| Email delivery / S3 storage | Not implemented | Local storage backend only |
