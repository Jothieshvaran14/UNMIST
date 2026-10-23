# Security

This document describes the security posture of the Unmist backend: what is
implemented, what is a documented limitation, and what is deliberately not
claimed. It is written to be verifiable — every "implemented" control has code
and, where applicable, a test.

## Threat model (what we defend against)

- **Credential theft** — passwords and refresh tokens must not leak from the DB.
- **Token forgery / replay** — access tokens must be verifiable and short-lived;
  refresh tokens must be revocable and single-use.
- **Broken object-level authorization (BOLA/IDOR)** — a valid user must not read
  or mutate another tenant's data by guessing IDs.
- **SSRF** — the retrieval feature must not be usable to reach internal
  networks, cloud metadata endpoints, or loopback services.
- **Data fabrication** — the system must not present invented content as
  verified knowledge or evidence.
- **Unbounded resource use** — retrieval and sandbox execution must be bounded
  in size and time.

## Authentication

Implemented in `security/passwords.py`, `security/tokens.py`,
`services/auth.py`:

- **Argon2id** password hashing (memory-hard KDF; cost parameters are
  configurable and validated at startup).
- **Access tokens** are short-lived HS256 JWTs with `sub`, `iat`, `nbf`, `exp`,
  `aud`, `typ`, and `jti` claims. `typ` is checked on decode so a refresh token
  can never be used as an access token.
- **Refresh tokens** are opaque, high-entropy values. **Only their SHA-256
  hash is stored**, so a DB leak does not expose usable tokens.
- **Refresh rotation**: each use issues a new refresh token and revokes the old
  one. Reuse of an already-rotated token is treated as theft and revokes the
  family.

### Limitations

- **HS256 with a shared secret.** This is symmetric; every verifier must hold
  the secret. For multi-service or zero-trust deployments, migrate to asymmetric
  signing (RS256/ES256) or an internal token service. Not implemented here.
- **No MFA, no password reset, no account lockout/rate limiting** at the app
  layer. Deploy behind a rate-limiting proxy and add these before production
  exposure. Not implemented here.
- **Token revocation is refresh-only.** Access tokens are stateless and remain
  valid until expiry (default 15 min). There is no access-token denylist.

## Authorization (multi-tenant isolation)

Implemented in `security/dependencies.py`, `security/rbac.py`,
`services/orgs.py`:

- Roles: **owner / admin / member**, resolved **server-side** on every
  org-scoped route via an `OrgRole` dependency.
- Resource access is always scoped by the caller's tenant; IDs alone never grant
  access.

### Document endpoints (processing, OCR regions, segmentation)

`api/v1/documents.py` applies the same rule to every document-scoped route,
including the Checkpoint E segmentation endpoints
(`POST /documents/{id}/segment`, `GET .../segmentation`,
`GET .../segments`, `POST .../segments/{sid}/review`):

- **Ownership is checked server-side on every request**, including the segment
  review mutation. The document is resolved against the caller's
  `owner_user_id` first, and the segment is then matched to that document —
  so a segment id alone never grants access.
- **Cross-tenant access returns `404`, not `403`.** A `403` would confirm that
  a guessed id exists; `404` gives no existence signal. Verified in
  `test_documents_segmentation.py` S22 and `test_documents_ocr_pipeline.py`.
- **Review mutations are audited** (`document.segment_review`) with the actor,
  resource id, review action, resulting revision, and the segment's
  type/uncertainty, through the same redacting `services/audit.py` used
  everywhere else.
- **Review is append-only.** Corrections write a new `segment_reviews` row and
  bump `revision`; the segment row records the current value and reviewer.
  Segmentation runs can never delete or overwrite a reviewed segment, so a
  reprocess cannot launder away a human correction.
- **Responses carry no storage keys, filesystem paths, or stack traces.**
  Failures surface a bounded category plus a short, sanitized detail string.

### Limitations

- Authorization is enforced in the service layer by convention. There is no
  centralized query-level row-filtering middleware, so a future route that
  forgets the dependency would be a BOLA risk. Mitigated by review + tests, not
  by a hard framework guarantee.

### Cross-tenant defects found and fixed (Checkpoint F)

The Checkpoint F integration audit reviewed the *non-document* read surfaces
against the same standard. Two broke it. Both were reproduced against the live
app before the fix and re-verified as closed afterwards, and both are now
guarded by `backend/tests/test_cross_tenant_authorization.py`.

**1. `GET /api/v1/topics` leaked every user's personal topics.**
`graph_service.list_topics` filtered only on `org_id IS NULL`, while
`create_topic` hardcodes `org_id=None` — so that predicate matched every
personal topic in the database regardless of owner. *Before:* a second user's
listing returned the first user's private topic. *After:* listing goes through
the same `authorize_topic_access` rule as single-topic reads — personal topics
to their creator, org topics to members — and the pagination total is scoped
with them.

**2. Question/assessment routes had no object-level authorization.**
`GET /api/v1/questions/{id}` was a bare `session.get`, returning another
tenant's question **including `reference_answer`, the grading key**. The same
hole existed on `GET`/`POST /questions/{id}/rubrics` and `POST /assessments`.
*After:* every question route resolves through its topic via a single shared
`graph_service.authorize_topic_access`, and `POST /questions` additionally
refuses to attach a question to a topic the caller cannot write to.

This is the concrete form of the BOLA risk described under Limitations above:
the rule existed and was applied on documents and single-topic reads, but had
simply not been applied to these routes. The fix moves the decision into the
service layer so the knowledge and learning routes cannot drift apart again.

## SSRF defense (retrieval)

Implemented in `services/knowledge/retrieval.py`, tested in
`backend/tests/test_retrieval.py`:

- **Deny by default.** An empty `RETRIEVAL_ALLOWED_HOSTS` allowlist blocks all
  outbound retrieval.
- **Scheme allowlist**: only `http`/`https`.
- **Host allowlist**: exact host match against the configured set.
- **IP validation after DNS resolution**: the resolved addresses are checked
  with `ipaddress.is_global`; private, loopback, link-local, reserved,
  multicast, and otherwise non-global addresses are rejected. A literal
  non-global IP in the URL is rejected before resolution.
- **Redirect re-validation**: every redirect hop is re-checked for scheme, host,
  and IP, and the redirect count is bounded.
- **robots.txt** is respected (self-contained parser; the stdlib parser is
  unavailable in this Python build).
- **Bounded responses**: connection/read timeouts and a streaming max-size cap
  prevent unbounded reads.
- **No credentials forwarded**: no cookies, auth headers, or proxies are attached
  to outbound requests; the User-Agent is explicit, not disguised.

### Limitations

- **DNS rebinding is mitigated, not eliminated.** We resolve and then let
  `httpx` resolve again for the actual connection. A fully hardened client pins
  the resolved IP for the connection. Not implemented here.
- **robots.txt failures fail open** (an unreachable robots.txt is treated as
  "no rules"). This favors availability; strict deployments may want fail-closed.

## Data-handling & privacy

- **Audit trail** (`services/audit.py`): append-only `audit_events` with
  **secret redaction** before persistence.
- **Secrets** are read from environment variables; `.env` is git-ignored and
  `.env.example` carries no real values.
- **Production startup checks** (`config.py`): a placeholder or short
  `SECRET_KEY` in production **aborts startup** instead of running insecurely.
- **No fabricated data**: extraction and diagnosis return `insufficient_evidence`
  rather than guessing; model-generated content is kept separate from verified
  graph content and labeled.
- **Private document storage** (`services/storage.py`): uploaded originals and
  derived page artifacts are stored under app-generated opaque keys — never the
  user's filename — in a private local root. The store has no public URLs, no
  listing, and is only reachable through an authorized API path. Originals are
  immutable (`put` refuses overwrite) and reads verify the content hash.
- **Untrusted uploads**: format is decided by magic bytes and confirmed by a
  real parser, never by the client's MIME type or filename; the extension must
  agree or the upload is rejected. Oversized bodies are rejected while
  streaming. PDFs are rasterized by a licensed backend that does not execute
  embedded content or fetch external resources.
- **Processing state is bounded**: failures persist only a coarse error
  category plus a sanitized detail — never raw exception text, file contents,
  storage keys, or stack traces — and the processing-status API exposes only
  lifecycle facts.
- **OCR results are traceable and owner-scoped** (`ocr_pipeline.py`,
  `api/v1/documents.py`): recognized regions carry model provenance (provider,
  model id, processing version) and are served only through an owner-authorized
  endpoint that never returns storage keys or paths. TrOCR reports no calibrated
  confidence, so regions store `NULL` rather than a token score mislabelled as
  correctness. Line crops are processed in-process; no page image or crop is
  sent to a third-party API, and the model runs locally. Recognition output is
  stored as *unverified* machine text, separate from any human-confirmed
  correction (a reviewed region is never overwritten by a re-run).

### Limitations

- **No encryption at rest or field-level encryption** beyond what the database
  provides. TLS in transit is expected to be terminated at the proxy/load
  balancer. Not implemented in-app.
- **No PII export/erasure (GDPR) endpoints.** Not implemented.
- **No document deletion or retention enforcement at all.** Confirmed by the
  Checkpoint F audit: there is no `DELETE` route, no service function and no job
  handler that ever reaches `deletion_pending`/`deleted`, and
  `ObjectStore.delete()` has no production caller. `Org.data_retention_days` is
  a stored column nothing reads. The skip-guards that would stop a stale worker
  resurrecting a deleting document are implemented and tested, but they are
  unreachable — the entry point that would set those states does not exist.
  Private storage therefore has no erasure story, and originals are written but
  never removed.

## Code sandbox

Implemented in `services/learning/sandbox.py`:

- Submissions run in a **separate subprocess** with a stripped environment,
  a wall-clock timeout, and a static deny-list of dangerous imports
  (`os`, `subprocess`, `socket`, `ctypes`, etc.).

### Limitations — read before trusting this

- **This is a baseline, not a hardened multi-tenant isolation boundary.** It is
  a subprocess with an import deny-list, **not** a container, gVisor, Firecracker,
  or seccomp jail. Determined code can escape an import deny-list (e.g. via
  `__import__`, `importlib`, or built-in object traversal).
- **Do not run untrusted third-party code in production on this sandbox.** For
  that, integrate a real isolation boundary. Not implemented here.

## Dependency & supply-chain posture

- Dependencies are **pinned** in `requirements.txt`.
- No lockfile/hash-pinning or automated dependency scanning is configured. Not
  implemented here.

## Known gaps (explicitly not implemented)

| Gap | Risk | Recommendation |
| --- | --- | --- |
| No rate limiting / lockout | Brute force, credential stuffing | Add at proxy + app layer |
| No MFA | Account takeover | Add TOTP/WebAuthn |
| No access-token denylist | Stolen access token valid until expiry | Keep TTL short; add denylist if needed |
| HS256 shared secret | Secret sprawl | Migrate to RS256/ES256 |
| Sandbox not hardened | Code escape | Use container/VM isolation for untrusted code |
| DNS rebinding not fully pinned | SSRF via rebind | Pin resolved IP per connection |
| No encryption at rest | Data exposure on disk leak | Rely on disk/volume encryption |
| No GDPR export/erasure | Compliance | Add subject-access/erasure endpoints |
| No CI security scanning | Regressions ship unnoticed | Add SAST + dependency scanning |

## Reporting

This is a pre-release foundation. If you find a vulnerability, please open a
private security advisory rather than a public issue.
