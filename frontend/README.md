# Unmist Frontend

React + TypeScript + Vite single-page client for the Unmist API. Built against the
backend's real contracts — every screen reads live data, and anything the API does not
expose is stated in the UI rather than faked.

> **Status: demo build.** This is a hackathon v1. It is not production-ready. See
> [Known gaps](#known-gaps).

## Quick start

```bash
# 1. install
npm install

# 2. start the backend (repo root, separate shell)
python -m alembic upgrade head
python -m uvicorn app.main:app --app-dir backend --reload   # port 8000
python -m app.workers.runner                                # background worker

# 3. start the frontend (this directory)
npm run dev        # http://localhost:5173
```

No CORS configuration is required for local development: Vite proxies `/api` to
`http://localhost:8000`, so the browser stays on one origin.

### Scripts

| Command | What it does |
| --- | --- |
| `npm run dev` | Dev server with hot reload on `:5173` |
| `npm run build` | Typecheck (`tsc -b`) then production bundle into `dist/` |
| `npm run preview` | Serve the production bundle on `:4173` (also proxies `/api`) |
| `npm run typecheck` | Typecheck only |

## Configuration

Copy `.env.example` to `.env.local` if you need to override anything. All `VITE_*` values
are baked into the browser bundle and are therefore **public** — never put credentials or
private keys in them.

| Variable | Default | Purpose |
| --- | --- | --- |
| `VITE_API_BASE_URL` | *(unset → same origin + `/api`)* | API base **up to but not including `/v1`**. E.g. `https://host/api`. |
| `VITE_API_PROXY_TARGET` | `http://localhost:8000` | Dev/preview proxy target when the base is unset. |
| `VITE_API_TIMEOUT_MS` | `20000` | Request timeout. |
| `VITE_PERSIST_REFRESH` | `1` | Set `0` for a fully memory-only session (re-login on every reload). |

## Session handling

- **Access token: memory only.** Never written to `localStorage` or `sessionStorage`.
- **Refresh token: `sessionStorage`** (per-tab, cleared when the tab closes), so a page
  reload during a demo does not force a re-login. Set `VITE_PERSIST_REFRESH=0` to disable.
- A 401 triggers exactly one refresh-and-retry; if that fails the session is cleared and
  the app returns to `/login`.

## Routes

| Route | Screen |
| --- | --- |
| `/` | Overview |
| `/documents` | Document library (upload, filter, search) |
| `/documents/:documentId` | Document detail + processing timeline |
| `/documents/:documentId/segments` | Segment review workspace |
| `/knowledge` | Knowledge Map |
| `/assessments` | Assessments |
| `/retention` | Retention |
| `/settings` | Settings |
| `/login` | Sign in / register |
| `*` | Not found |

All routes use browser history, so direct navigation and refresh work. Secondary screens
are code-split.

## API surface used

`POST /v1/auth/register`, `POST /v1/auth/login`, `POST /v1/auth/refresh`,
`POST /v1/auth/logout`, `POST /v1/auth/change-password`, `GET /v1/auth/me`,
`GET /v1/health`, `POST /v1/documents/uploads`, `GET /v1/documents`,
`GET /v1/documents/{id}`, `GET /v1/documents/{id}/processing`,
`GET /v1/documents/{id}/regions`, `POST /v1/documents/{id}/segment`,
`GET /v1/documents/{id}/segmentation`, `GET /v1/documents/{id}/segments`,
`POST /v1/documents/{id}/segments/{sid}/review`, `GET /v1/topics`, `POST /v1/topics`,
`GET /v1/topics/{id}/graphs`, `GET /v1/learners/me/reviews/due`, `GET /v1/me/profile`.

## Known gaps

These are backend limitations. The UI reports them rather than working around them with
fabricated data:

- **No OCR trigger endpoint.** `POST /v1/documents/{id}/ocr` does not exist, so OCR never
  starts automatically after upload. A freshly uploaded document stops at `preprocessed`
  and segmentation correctly refuses with a conflict.
- **No question catalogue.** The API cannot list questions, so the UI offers no
  "start assessment" action.
- **No schedule mutation.** Retention schedules cannot be edited or suspended via the API.
- **No node/edge list.** `GET /v1/topics/{id}/graphs` returns counts only, so the Knowledge
  Map shows topic and version summaries instead of a drawn graph.
- **No account management.** No endpoints to rename an account, delete data, or export
  records; Settings lists these as unavailable instead of showing dead buttons.
- Search and filtering run on the records loaded from the server — the documents API has
  no server-side search parameter.

## Privacy notes

- Storage keys and filesystem paths are never returned by the API and are never rendered.
- Uploaded files are sent only to the configured API origin; nothing goes to a third party.
- Errors surface the server's bounded messages; stack traces and internals are never shown.
