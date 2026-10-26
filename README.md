# Unmist

**Evidence-based knowledge intelligence for learners and institutions.**

Unmist turns source material into an auditable knowledge graph, then uses that
graph to drive assessment, mastery estimation, prerequisite-gap diagnosis, and
spaced retention. Every generated statement is tied back to a cited source
passage; when evidence is missing the system returns `insufficient_evidence`
instead of guessing.

---

## Problem statement

Learners and instructors work across documents, quizzes, and revision schedules
that rarely connect to each other. Study tools tend to produce answers without
showing where those answers came from, and there is no reliable way to see which
prerequisite a learner is actually missing.

Unmist is built for:

- **Learners** — review material with citations back to the original passage,
  see mastery state, and get told which prerequisite gap is blocking progress.
- **Instructors / institutions** — ingest material they are permitted to use,
  keep an auditable graph of what was derived, and assess with deterministic
  checks rather than opaque scoring.

The design principle throughout is that **the system must not invent evidence**.
Where a capability does not exist, the API and the UI say so explicitly instead
of returning a plausible-looking guess.

---

## Current status

This is a working prototype with a tested backend and a functional web client.
It is **not** production-ready. See [Known limitations](#known-limitations) —
several items there are load-bearing, not decoration.

| Area | State | Notes |
| --- | --- | --- |
| Auth (register/login/refresh/logout) | ✅ working | Argon2id passwords, HS256 access tokens, opaque rotating refresh tokens with reuse detection |
| Organizations & RBAC | ✅ working | Owner/admin/member roles, server-side authorization on every org route |
| Source & license enforcement | ✅ working | Ingestion blocked unless the source license permits it |
| SSRF-safe retrieval | ✅ working | Host allowlist, private-IP blocking, robots.txt, size/redirect/time limits |
| Deterministic extraction | ✅ working | Sentence/keyword baseline; LLM path fails loudly when unconfigured |
| Knowledge graph build & versioning | ✅ working | Immutable published versions, validation, rollback |
| Durable job queue | ✅ working | DB-backed, idempotent, claim/lease/run semantics |
| Document upload & preprocessing | ✅ working | Magic-byte validation, image normalization, optional PDF rasterization |
| Questions, rubrics, grading | ✅ working | Deterministic checks (numeric/exact/contains/regex/code/prose) |
| Question/answer segmentation | ✅ working | Deterministic rule baseline over OCR regions; no accuracy claim |
| Spaced retention | ✅ working | SM-2-style scheduling, due-review queries |
| Portfolios & public profiles | ✅ working | Curated items, public read-only endpoint |
| Audit log | ✅ working | Append-only events with secret redaction |
| Migrations | ✅ working | 5 chained revisions, single head, verified against SQLite |
| Web frontend | ✅ working | React SPA covering all primary screens; see below |
| OCR / handwriting | ⚠️ provisional | Provider-neutral pipeline implemented; TrOCR is opt-in and **unverified here** |
| Mastery estimation (BKT) | ⚠️ provisional | Bayesian Knowledge Tracing, **uncalibrated** (`provisional-v1`) |
| Code sandbox | ⚠️ baseline | Subprocess isolation; **not** a hardened multi-tenant sandbox |
| PostgreSQL | ⚠️ untested here | ORM is dialect-agnostic; only SQLite exercised in this environment |
| LLM integration | ❌ not configured | `LLM_PROVIDER=none` default; boundary fails loud by design |
| Document deletion | ✅ working | `DELETE /documents/{id}` → purge job → tombstone + audit; UI confirm modal reports success only on 404 |
| Document OCR trigger (UI) | ✅ working | `POST /documents/{id}/ocr` wired to the detail page; eligibility/conflict/pending states mirror the API |
| CI security scanning | ⚠️ partial | Dependency audits run in CI (`npm audit` prod gate, informational `pip-audit`); **no SAST** |

Per-feature detail with test evidence: [`docs/product/feature-status.md`](docs/product/feature-status.md).

---

## Architecture

Modular monolith with three runtime pieces:

```
Browser ──> React SPA (Vite, :5173)
              │  dev proxy /api ──┐
              ▼                   ▼
        FastAPI app (:8000) ──> SQLAlchemy ──> SQLite (dev) / PostgreSQL (prod)
              │                                    ▲
              │  enqueues jobs                     │ claim + lease
              ▼                                    │
        Background worker (app.workers.runner) ────┘
              │
              └──> Object store (local disk, private, content-hashed)
```

- **API layer** — FastAPI routers mounted under `/api/v1`, thin; authorization
  is a dependency, not an afterthought.
- **Service layer** — business logic (ingestion, extraction, graph, grading,
  mastery, retention, documents) lives in `backend/app/services/`.
- **Worker** — polls a durable DB-backed queue and runs `document.process`,
  `document.ocr`, `document.segment`, and `graph.build` with lease, retry, and
  cancellation semantics.
- **Storage** — originals and derived page images under `data/storage/`, never
  exposed through the API (no storage key or path is ever returned).

Component map: [`docs/architecture.md`](docs/architecture.md). Requirement →
code → test mapping: [`docs/requirements-traceability.md`](docs/requirements-traceability.md).

---

## Technology stack

**Backend**

| Layer | Choice |
| --- | --- |
| Language | Python 3.10+ |
| Web framework | FastAPI 0.144 / Starlette 1.3 |
| Validation | Pydantic 2.12 |
| ORM | SQLAlchemy 2.0 |
| Migrations | Alembic 1.11 |
| Database | SQLite (dev/test), PostgreSQL (production, unvalidated here) |
| Passwords | Argon2id via `argon2-cffi` |
| Tokens | HS256 JWT (`python-jose`) + opaque rotating refresh tokens |
| HTTP client | `httpx` (SSRF-guarded retrieval) |
| Documents | Pillow (images), PyPDF2 (validation), optional `pypdfium2` (PDF rasterization) |
| OCR (optional) | `torch` + `transformers` (TrOCR), CPU-capable |
| Server | uvicorn |

**Frontend** (`frontend/`)

| Layer | Choice |
| --- | --- |
| Framework | React 18 + TypeScript 5.6 |
| Build | Vite 5 |
| Routing | react-router-dom 6 |
| Icons | lucide-react |
| Styling | Plain CSS with design tokens (no UI framework) |

**Tooling**: flake8, isort, mypy, pytest, pytest-cov.

---

## Repository structure

```
.
├── backend/
│   ├── app/
│   │   ├── api/v1/          # routers: auth, orgs, knowledge, learning, profiles, documents
│   │   ├── models/          # SQLAlchemy models (33 tables)
│   │   ├── schemas/         # Pydantic request/response contracts
│   │   ├── security/        # passwords, tokens, RBAC
│   │   ├── services/        # business logic
│   │   │   ├── documents/   # ingestion, preprocess, ocr/, segmentation/
│   │   │   ├── knowledge/   # extraction, graph, retrieval, sources
│   │   │   ├── learning/    # grading, mastery, diagnosis, sandbox
│   │   │   ├── evaluation/  # CER / WER reports
│   │   │   └── retention/   # spaced scheduling
│   │   └── workers/         # queue runner + job handlers
│   └── tests/               # 20 test modules
├── frontend/                # React + Vite SPA (own README, .gitignore)
├── migrations/              # Alembic revisions
├── scripts/                 # download_trocr_model, evaluate_ocr, smoke/verify helpers
├── docs/                    # architecture, security, testing, feature status, traceability
├── infrastructure/          # Terraform scaffolding (not a supported deployment path)
├── .env.example             # configuration template
└── requirements.txt         # pinned backend dependencies
```

---

## Data flow & API surface

All routes are mounted under `/api/v1`. Interactive OpenAPI docs are served at
`/docs` when the server runs.

**Auth** — `POST /auth/register`, `POST /auth/login`, `POST /auth/refresh`,
`POST /auth/logout`, `POST /auth/change-password`, `GET /auth/me`

**Organizations** — `POST /orgs`, `GET /orgs/{id}`, `GET /orgs/{id}/members`,
`POST /orgs/{id}/members`, `PATCH /orgs/{id}/members/{user_id}`,
`DELETE /orgs/{id}/members/{user_id}`

**Knowledge** — `POST /topics`, `GET /topics`, `GET /topics/{id}`,
`POST /topics/{id}/builds`, `GET /topics/{id}/builds/{build_id}`,
`GET /topics/{id}/graphs`, `POST /topics/{id}/graphs/rollback`

**Learning** — `POST /questions`, `GET /questions/{id}`,
`POST /questions/{id}/rubrics`, `POST /assessments`,
`POST /assessments/{id}/submit`, `GET /learners/me/mastery`,
`POST /learners/me/diagnoses`, `GET /learners/me/reviews/due`

**Profiles** — `GET /me/profile`, `GET|PATCH /me/portfolio/settings`,
`GET|POST /me/portfolio/items`, `GET /portfolios/{public_id}`

**Documents** — `POST /documents/uploads`, `GET /documents`,
`GET /documents/{id}`, `DELETE /documents/{id}`,
`GET /documents/{id}/processing`, `POST /documents/{id}/ocr`,
`GET /documents/{id}/regions`, `POST /documents/{id}/segment`,
`GET /documents/{id}/segmentation`, `GET /documents/{id}/segments`,
`POST /documents/{id}/segments/{sid}/review`

**System** — `GET /api/v1/health`, `GET /healthz`

### Document lifecycle (actual behaviour)

```
upload ──> queued ──> preprocessed ──> ocr_processing ──> segmentation
              │            │                 │                 │
              └ worker     └ page images     └ POST /ocr       └ POST /segment
                                            (recognized content, not verified truth)

DELETE /documents/{id} ──> deletion_pending ──> document.purge job ──> deleted
                            (pipelines skip)    (storage + rows purged,   (tombstone:
                                                 lease-checked)            reads 404)
```

Upload enqueues `document.process`; the worker validates, stores privately, and
preprocesses to page images. `POST /documents/{id}/ocr` schedules recognition
(eligibility-checked: 409 before preprocessing; audited; idempotent while a run
is in flight), and `POST /documents/{id}/segment` schedules segmentation on its
own status axis. `DELETE /documents/{id}` begins a one-way, asynchronous purge:
the document's private storage objects and derived rows are destroyed by the
`document.purge` job, and only then does it become `deleted`.

---

## Machine-learning / NLP components

Only these are genuinely implemented; each is scoped honestly.

| Component | What it actually does | Status |
| --- | --- | --- |
| **TrOCR handwriting OCR** (`services/documents/ocr/trocr.py`) | Line-level recognition of individual text lines via `transformers`, CPU-capable, lazy-loaded | ⚠️ opt-in and **unverified in this environment** — the weights were not downloadable, so no inference result is claimed |
| **Line detection** (`ocr/lines.py`) | Projection-profile text-line extraction using Pillow only | ✅ tested |
| **Text normalization** (`ocr/normalization.py`) | Explicit, versioned whitespace/sign normalization; keeps units | ✅ tested |
| **Rule-based segmentation** (`segmentation/rules.py`) | Deterministic English/marker heuristics grouping OCR regions into question/subpart/answer candidates | ✅ tested — **no precision/recall claim, no labeled benchmark** |
| **Deterministic extraction** (`knowledge/extraction.py`) | Sentence/keyword baseline producing claims with passage citations | ✅ tested |
| **Concept extraction** (`knowledge/concept_extraction.py`) | Heuristic concepts and prerequisite edges | ✅ tested |
| **Bayesian Knowledge Tracing** (`learning/mastery.py`) | Mastery update with fixed default parameters | ⚠️ **uncalibrated** (`provisional-v1`) |
| **Diagnosis** (`learning/diagnosis.py`) | Competing hypotheses + information-gain question selection | ✅ tested |
| **SM-2 retention** (`retention/scheduling.py`) | Spaced-repetition scheduling | ✅ tested |
| **CER/WER evaluation** (`evaluation/`) | Reproducible JSON accuracy reports; refuses to score without a stated dataset license | ✅ tested |

**No accuracy figure, benchmark, or competitor-parity claim is made anywhere in
this repository**, because no labeled benchmark with verified training-data
terms has been run.

Not implemented: LLM extraction (`LLM_PROVIDER=none` fails loud), equation
recognition, table/diagram reading, layout reconstruction, handwriting grading.

---

## Database & external services

- **Database**: SQLite by default (`./data/unmist.db`); PostgreSQL via
  `DATABASE_URL`. Only SQLite has been exercised here.
- **Object storage**: local filesystem backend (`./data/storage`). No S3.
- **External services**: none required to run. Outbound retrieval is
  **disabled unless `RETRIEVAL_ALLOWED_HOSTS` is set** (empty = deny all).
- **Email**: no transactional email is wired up.

---

## Getting started

### Prerequisites

- Python 3.10+
- Node.js 18+ and npm

### 1. Backend

```powershell
# PowerShell (Windows)
pip install -r requirements.txt
$env:PYTHONPATH = "backend"
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --app-dir backend
```

```bash
# Bash (macOS/Linux)
pip install -r requirements.txt
export PYTHONPATH=backend
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --app-dir backend
```

API is at <http://127.0.0.1:8000>, OpenAPI UI at <http://127.0.0.1:8000/docs>,
health at <http://127.0.0.1:8000/api/v1/health>.

Out of the box this uses SQLite, creates `./data/unmist.db`, and applies a
development-only fallback signing key. **That fallback is refused when
`ENVIRONMENT=production`** — see below.

### 2. Worker

The API enqueues jobs but does not execute them. Run the worker in a second
terminal:

```powershell
$env:PYTHONPATH = "backend"
python -m app.workers.runner
```

Useful flags: `--once` (process one job and exit), `--list` (list job types),
`--type <job>` (filter to one job type).

### 3. Frontend

```powershell
cd frontend
npm install
npm run dev
```

Opens at <http://localhost:5173>. The Vite dev server proxies `/api` to
`http://localhost:8000`, so no backend CORS configuration is needed.

### 4. Optional extras

```powershell
pip install -r requirements.txt            # base (images only)
pip install -e ".[pdf]"                    # + pypdfium2 for PDF rasterization
pip install -e ".[ocr]"                    # + torch/transformers for TrOCR
```

Without the `pdf` extra, PDFs needing rasterization fail with an explicit
`pdf_renderer_unavailable` error. Without the `ocr` extra,
`OCR_PROVIDER=trocr` fails with `ocr_unavailable` — both fail loudly rather
than returning a wrong result.

---

## Configuration

All configuration is read from **process environment variables**, validated at
import time, and documented in [`.env.example`](.env.example).

> **Note:** the application does **not** auto-load a `.env` file — `python-dotenv`
> is a dependency but no code calls `load_dotenv()`. `.env.example` is a
> reference template listing variable names and safe placeholder values. Set
> variables in your shell (as shown above) or through your process manager.

Invalid or unsafe configuration causes the app to **refuse to start** rather
than run insecurely. Highlights:

| Variable | Default | Notes |
| --- | --- | --- |
| `ENVIRONMENT` | `development` | One of `development`, `test`, `staging`, `production` |
| `SECRET_KEY` | dev fallback | In production, a placeholder or a value under 32 chars causes startup to fail |
| `DATABASE_URL` | `sqlite:///./data/unmist.db` | PostgreSQL for production |
| `STORAGE_LOCAL_DIR` | `./data/storage` | Private object store |
| `OCR_PROVIDER` | `none` | `fake` is a test double and is **refused in production** |
| `LLM_PROVIDER` | `none` | Fail-loud boundary; no LLM is called unless configured |
| `RETRIEVAL_ALLOWED_HOSTS` | *(empty)* | **Empty disables all outbound retrieval** (SSRF-safe default) |
| `UPLOAD_MAX_BYTES` | 20 MB | Enforced before bytes are accepted |
| `UPLOAD_ALLOWED_FORMATS` | `pdf,jpg,jpeg,png,tiff` | Plus magic-byte verification |
| `ACCESS_TOKEN_TTL_SECONDS` | 900 | |

Generate a production key with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

---

## Commands

```powershell
# --- Backend ---
pip install -r requirements.txt            # install dependencies
$env:PYTHONPATH="backend"
python -m alembic upgrade head             # apply migrations
python -m uvicorn app.main:app --reload --app-dir backend   # API on :8000
python -m app.workers.runner               # background worker

# --- Quality gates (same as CI) ---
python -m flake8 backend scripts                                     # lint
python -m isort --profile black --line-length 100 --check-only backend/app backend/tests scripts
python -m mypy backend/app --ignore-missing-imports                  # types
python -m pytest backend/tests -q                                    # tests
python scripts/verify_migration.py data/unmist.db                    # migration drift

# --- Frontend ---
cd frontend
npm install
npm run dev                                    # dev server on :5173
npm run typecheck                              # tsc -b
npm run build                                  # production build to dist/
```

**Deployment:** there is no supported production deployment path in this
repository — no Dockerfile, no published images, no infrastructure-as-code
that has been applied. `infrastructure/` contains Terraform scaffolding that is
untested. Treat local/VM deployment behind a hardened reverse proxy as the only
option, and read the security notes below first.

---

## Testing

```powershell
$env:PYTHONPATH="backend"
python -m pytest backend/tests -q
```

Last verified run on Python 3.10 / SQLite: **281 passed, 1 skipped** (~115s),
and the same result on Linux + Python 3.10 inside a container that emulates the
CI steps end to end. The single skip is `test_documents_ocr_smoke.py`, an
opt-in real-model test gated behind `UNMIST_OCR_SMOKE=1` and
`OCR_REAL_MODEL_SMOKE_ENABLED=1` — ordinary CI never loads a model.

CI (`.github/workflows/ci.yml`) runs two jobs on pushes to `main` and on pull
requests:

- **`checks` (Python 3.10)** — flake8, isort, mypy, `alembic upgrade head` +
  `scripts/verify_migration.py`, the full pytest suite (installs the documented
  `pdf` extra so real PDF rasterization stays covered), and an informational
  `pip-audit`.
- **`frontend` (Node 20)** — `npm ci`, `npm run typecheck`, `npm run build`, a
  **gating** `npm audit --omit=dev --audit-level=high`, and an informational
  full `npm audit`.

**Known testing gaps:**

- **No frontend tests.** The SPA is type-checked and built in CI, but has no
  unit or end-to-end test suite; frontend error handling is verified by
  type-checking, production build, and manual end-to-end runs only.
- **No SAST or fuzz testing** in CI — dependency audits are the only
  security-oriented CI gate.
- **PostgreSQL is untested** in this environment — only SQLite has been run.
- **No real-document segmentation accuracy measurement** — no labeled benchmark
  exists.

Test catalogue: [`docs/testing.md`](docs/testing.md).

---

## Security

See [`docs/security.md`](docs/security.md) for the threat model and full detail.

**What is implemented:**

- Argon2id password hashing; HS256 access tokens with short TTL; opaque refresh
  tokens stored hashed, with rotation and reuse detection.
- Server-side authorization on every tenant-scoped route (document and topic
  access are owner-scoped; a non-owner gets 404 with no ID disclosure).
- SSRF-guarded retrieval: host allowlist, private/loopback/link-local IP
  blocking, redirect limits, robots.txt, response size caps.
- Upload validation by magic bytes and real parsers, with size/type/page limits
  enforced before bytes are accepted.
- Startup configuration validation — placeholder secrets and test doubles are
  refused in production.
- Audit log with secret redaction; storage keys and filesystem paths are never
  returned by the API.
- Cross-tenant authorization defects found by audit are fixed and covered by
  `backend/tests/test_cross_tenant_authorization.py`.

**Read before trusting it:**

- **Not production-secure.** Controls exist; that is not the same as being
  production-ready. There is no MFA, no password reset, no account lockout, and
  **no rate limiting** at the application layer — deploy behind a rate-limiting
  proxy.
- The code sandbox is a **baseline** subprocess isolation, not a reviewed
  multi-tenant boundary. Do not execute untrusted code on it.
- DNS-rebinding defense is mitigated, not eliminated.
- PostgreSQL authorization semantics are unvalidated; the SQLite queue uses
  `.with_for_update()`, which is a no-op on SQLite, so concurrent SQLite workers
  could double-claim jobs.
- **Dependency advisories:** `npm audit` reports 2 moderate advisories in
  `react-router` 6.x (open redirect via `<Link>`/`useNavigate`, and an SSR
  hydration issue). This app is a pure SPA with no SSR and does not consume
  return-to URLs from query parameters, so neither is reachable through current
  code paths — but the fix requires a major upgrade to react-router 7 and has
  **not** been applied or validated.
- **Dependency scanning runs in CI; SAST does not.** The frontend job gates on
  `npm audit --omit=dev --audit-level=high` and reports a full `npm audit`
  informationally; the backend job reports `pip-audit` informationally (it
  cannot gate on pinned, hash-verified requirements without false positives).
  There is no SAST, no secret scanning, and no fuzzing in CI.

---

## Known limitations

These are real and deliberately visible:

1. **Document deletion has no retention schedule.** `DELETE /documents/{id}`
   purges a document immediately (storage + derived rows + tombstone);
   `Org.data_retention_days` is stored but not enforced.
2. **No question-catalogue listing endpoint**, so the UI offers no "start
   assessment" action.
3. **Retention schedules cannot be mutated** via the API.
4. **Graph nodes/edges are not exposed** — only concept and relationship
   *counts*, so the Knowledge Map draws no graph.
5. **No account rename, delete, or data export.**
6. **No server-side document search** — filtering runs over loaded records.
7. **TrOCR inference is unverified** — weights were not obtainable in this
   environment; no OCR output has been produced or reviewed.
8. **Training-data terms for TrOCR's IAM fine-tuning corpus were not
   re-verified** (host unreachable). Confirm them before any commercial use.
9. **Mastery (BKT) is uncalibrated**, marked `provisional-v1`.
10. **Segmentation accuracy on real documents is unestablished** — heuristic
    rules, no labeled benchmark.
11. **PostgreSQL unvalidated**; **backup/restore not implemented**.
12. **No frontend test suite.**
13. **Live recognition in this environment was exercised with the sanctioned
    `fake` test double** (refused in production). The UI shows each region's
    real provider/model label — e.g. `fake/fake://fixture` — so a test double is
    never presented as TrOCR, and TrOCR itself remains unverified (item 7).
14. **Purge removes every stored object but leaves the empty per-document
    directory shell** on disk (`storage/documents/<id>/` contains no files
    afterwards). No content, storage key, or path is exposed by the API.

---

## Contributing

1. Fork and create a feature branch from `main`.
2. Keep changes scoped and covered by tests where behaviour changes.
3. Run the quality gates before opening a PR:
   `flake8`, `isort --check-only`, `mypy`, `pytest`.
4. Open a PR describing the change and how it was verified.

Report bugs and request features through **GitHub Issues** on this repository.
Please include the environment (OS, Python version), the command you ran, and
the observed vs. expected behaviour. Do not paste secrets, tokens, private
documents, or personal data into issues.

---

## License

**This repository does not carry a project-wide open-source license.**

No license file is provided, and no license is granted by publishing this
code. Public visibility of a repository does **not** grant permission to copy,
modify, or redistribute its contents — all rights are reserved by the
copyright holder unless a license is separately stated.

Third-party dependencies and models keep their own licenses, which are
unchanged by this repository — for example `torch` (BSD-3-Clause),
`transformers` (Apache-2.0), and the `microsoft/trocr-base-handwritten` weights
(MIT, per its model card). Review each dependency's license directly before
redistributing or commercializing.
