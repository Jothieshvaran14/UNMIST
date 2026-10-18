# Unmist

**Evidence-based knowledge intelligence for learners and institutions.**

Unmist turns licensed source material into an auditable knowledge graph, then
uses that graph to drive assessment, mastery estimation, prerequisite-gap
diagnosis, and spaced retention. Every generated statement is tied back to a
cited source passage; the system refuses to fabricate evidence and returns
`insufficient_evidence` rather than guessing.

> **Status: backend foundation, honest scope.**
> This repository currently contains a working, tested **backend** (FastAPI +
> SQLAlchemy + Alembic) covering auth, organizations, knowledge ingestion,
> deterministic graph building, assessment/grading, mastery, diagnosis,
> retention, and portfolios. It does **not** yet include a frontend, an
> OCR/handwriting pipeline, a configured LLM, or CI. See
> [`docs/product/feature-status.md`](docs/product/feature-status.md) for the
> precise, per-feature status.

---

## What works today

| Area | State | Notes |
| --- | --- | --- |
| Auth (register/login/refresh/logout) | ✅ working | Argon2id passwords, HS256 access tokens, opaque rotating refresh tokens |
| Organizations & RBAC | ✅ working | Owner/admin/member roles, server-side authorization on every org route |
| Source & license enforcement | ✅ working | Ingestion is blocked unless the source license permits it |
| SSRF-safe retrieval | ✅ working | Host allowlist, private-IP blocking, robots.txt, size/redirect/time limits |
| Deterministic extraction | ✅ working | Sentence/keyword baseline; LLM path fails loudly when unconfigured |
| Knowledge graph build & versioning | ✅ working | Immutable published versions, validation, rollback |
| Durable job queue | ✅ working | DB-backed, idempotent, claim/lease/run semantics |
| Questions, rubrics, grading | ✅ working | Deterministic checks (numeric/exact/contains/regex/code/prose) |
| Code sandbox | ⚠️ baseline | Subprocess isolation, **not** a hardened multi-tenant sandbox |
| Mastery estimation (BKT) | ⚠️ provisional | Bayesian Knowledge Tracing, **uncalibrated** (`provisional-v1`) |
| Prerequisite-gap diagnosis | ✅ working | Competing hypotheses, information-gain question selection |
| Spaced retention | ✅ working | SM-2-style scheduling, due-review queries |
| Portfolios & public profiles | ✅ working | Curated items, public read-only endpoint |
| Audit log | ✅ working | Append-only events with secret redaction |
| Alembic migrations | ✅ working | Initial schema (27 tables) verified against SQLite |
| CI (lint/type/test/migration) | ✅ working | GitHub Actions: flake8, isort, mypy, migration verify, pytest |
| LLM integration | ❌ not configured | `LLM_PROVIDER=none` default; boundary is fail-loud by design |
| OCR / handwriting | ❌ not implemented | — |
| Frontend | ❌ not implemented | — |

---

## Quickstart (local, SQLite)

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure (SQLite is the default; copy and edit for other settings)
Copy-Item .env.example .env

# 3. Apply migrations
$env:PYTHONPATH="backend"
python -m alembic upgrade head

# 4. Run the API
python -m uvicorn app.main:app --reload --app-dir backend
```

Open <http://127.0.0.1:8000/docs> for the interactive OpenAPI UI.

### Run the tests

```powershell
$env:PYTHONPATH="backend"
python -m pytest backend/tests -q
```

### Use PostgreSQL (production)

Set `DATABASE_URL` to a PostgreSQL URL (see `.env.example`), install a
PostgreSQL driver (`psycopg2-binary`), and run the same `alembic upgrade head`.
The ORM layer is dialect-agnostic; SQLite is used for dev/tests so the project
runs without external services.

---

## Configuration

All configuration is read from environment variables and validated at startup.
Invalid or unsafe configuration (for example a placeholder `SECRET_KEY` in
production) causes the app to **refuse to start** rather than run insecurely.
See [`.env.example`](.env.example) for the full contract. Highlights:

- `SECRET_KEY` — must be a long random value in production (>= 32 chars).
- `DATABASE_URL` — SQLite for dev/test, PostgreSQL for prod.
- `RETRIEVAL_ALLOWED_HOSTS` — comma-separated allowlist; **empty disables all
  outbound retrieval** (SSRF safety default).
- `LLM_PROVIDER` — `none` (default) keeps the LLM boundary fail-loud.

---

## Architecture

Modular monolith: FastAPI routers → service layer → SQLAlchemy models. See
[`docs/architecture.md`](docs/architecture.md) for the component map and
[`docs/requirements-traceability.md`](docs/requirements-traceability.md) for how
each requirement maps to code and tests.

## Security

See [`docs/security.md`](docs/security.md) for the threat model, auth design,
SSRF defenses, sandbox limitations, and known gaps.

## License

MIT — see [`LICENSE`](LICENSE). Note that the MIT license covers **this
codebase only**; ingested source content keeps its own license, which Unmist
enforces at ingestion time.
