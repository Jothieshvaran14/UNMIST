# Testing

## How to run

```powershell
$env:PYTHONPATH="backend"
python -m pytest backend/tests -q
```

Tests run against **SQLite** (in-memory-per-file) with foreign keys enforced,
so they need no external services. The suite sets its own test environment in
`conftest.py`; you do not need a `.env` file to run tests.

## Current state

Last verified full run: **69 passed, 0 failed** on Python 3.10 / SQLite.

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

## What is **not** covered (honest gaps)

- **No PostgreSQL run.** The ORM layer is dialect-agnostic, but only SQLite is
  exercised in this environment. A PostgreSQL integration job is not configured.
- **No load / performance tests.**
- **No fuzzing** of the retrieval or extraction parsers.
- **No security scanning** (SAST/dependency) — the CI pipeline runs
  lint/type/test/migration checks only (`.github/workflows/ci.yml`).
- **No frontend tests** — there is no frontend yet.
- **Sandbox escape testing.** The sandbox is a baseline subprocess; its
  resistance to a determined escape has not been adversarially tested. See
  [`security.md`](security.md).

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
