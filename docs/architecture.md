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

- `services/jobs.py` — enqueue (idempotent), claim with a lease, heartbeat,
  retry with backoff, dead-letter after max attempts.
- `workers/handlers.py` — a registry of job handlers (currently `graph.build`).
- `workers/runner.py` — a poll loop that claims and executes jobs.

This keeps the HTTP path fast and makes graph builds restartable.

## Data model (27 tables)

Grouped by domain:

- **Identity:** `users`, `refresh_tokens`, `organizations`, `organization_members`
- **Audit:** `audit_events`
- **Knowledge:** `sources`, `source_documents`, `source_passages`, `topics`,
  `concepts`, `concept_versions`, `concept_relationships`, `claims`,
  `graph_builds`, `graph_versions`, `learning_objectives`
- **Learning:** `questions`, `rubrics`, `assessment_attempts`,
  `criterion_results`, `mastery_estimates`, `diagnoses`
- **Retention:** `retention_schedules`, `review_attempts`
- **Portfolio:** `profiles`, `portfolio_items`
- **Jobs:** `jobs`

Schema changes go through **Alembic** (`migrations/versions/`). The initial
revision is verified against SQLite by `scripts/verify_migration.py`.

## Cross-cutting concerns

- **Config** (`config.py`) — env-driven, validated at import; unsafe values
  (placeholder `SECRET_KEY` in prod, etc.) abort startup.
- **Logging** (`logging.py`) — JSON logs with a per-request correlation ID.
- **Errors** (`errors.py`) — an `AppError` hierarchy mapped to consistent HTTP
  responses by handlers.
- **Audit** (`services/audit.py`) — append-only events with secret redaction.

## Persistence & dialects

SQLAlchemy 2.0 with a `UTCDateTime` type decorator normalizes datetimes so
SQLite (naive) and PostgreSQL (aware) behave identically. SQLite enables
foreign-key enforcement and WAL for local dev/tests; PostgreSQL uses normal
pooling. **Only SQLite is exercised in this environment** — PostgreSQL is
supported by the ORM layer but not verified here.

## What is intentionally out of scope (for now)

Frontend, OCR/handwriting, a configured LLM, CI/CD, and a hardened multi-tenant
code sandbox. Each has a fail-loud or clearly-marked boundary; see
[`docs/product/feature-status.md`](product/feature-status.md).
