/**
 * Typed endpoint functions — one function per real backend route.
 *
 * Every path, method, query parameter and response shape here was read from
 * `backend/app/api/v1/*.py` and `backend/app/schemas/*.py`. Endpoints that do
 * not exist in the backend are NOT declared here; the UI reports those as
 * unavailable instead of faking them.
 */

import { request, uploadWithProgress, type UploadProgress } from "./http";
import type {
  Diagnosis,
  DocumentProcessingStatus,
  DocumentSegment,
  GraphVersion,
  HealthResponse,
  LearnerProfile,
  LoginRequest,
  MasteryEntry,
  OcrRegion,
  Page,
  PortfolioItem,
  PortfolioItemCreate,
  PortfolioSettings,
  PortfolioSettingsUpdate,
  PublicProfile,
  RegisterRequest,
  ReviewItem,
  SegmentReviewRequest,
  SegmentReviewResponse,
  SegmentationStatus,
  SourceDocument,
  Topic,
  TokenResponse,
  UserProfile,
} from "@/types/api";

const V1 = "/v1";

/* ---------------------------- system ----------------------------- */

/**
 * `GET /api/v1/health`. Written relative to `/v1` like every other path so the
 * configured API base applies uniformly (it already includes the `/api` part).
 */
export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>(`${V1}/health`, { anonymous: true, signal });
}

/* ---------------------------- auth -------------------------------- */

export function login(payload: LoginRequest, signal?: AbortSignal): Promise<TokenResponse> {
  return request<TokenResponse>(`${V1}/auth/login`, {
    method: "POST",
    body: payload,
    anonymous: true,
    signal,
  });
}

/**
 * `POST /v1/auth/register` returns **`UserResponse` (201)**, not tokens.
 * Registration deliberately does not start a session — the caller must follow
 * it with `login()`. This function therefore returns the created user only.
 */
export function register(payload: RegisterRequest, signal?: AbortSignal): Promise<UserProfile> {
  return request<UserProfile>(`${V1}/auth/register`, {
    method: "POST",
    body: payload,
    anonymous: true,
    signal,
  });
}

export function logout(signal?: AbortSignal): Promise<void> {
  return request<void>(`${V1}/auth/logout`, { method: "POST", signal });
}

export function me(signal?: AbortSignal): Promise<UserProfile> {
  return request<UserProfile>(`${V1}/auth/me`, { signal });
}

/* ------------------------- documents ----------------------------- */

/**
 * Upload a document. Real byte progress is reported through `onProgress`;
 * nothing is fabricated if the browser cannot measure it.
 *
 * Backend contract: multipart/form-data with `file` and optional `org_id`.
 */
export function uploadDocument(
  file: File,
  opts: { orgId?: string; onProgress?: (p: UploadProgress) => void; signal?: AbortSignal } = {},
): Promise<SourceDocument> {
  const form = new FormData();
  form.append("file", file, file.name);
  if (opts.orgId) form.append("org_id", opts.orgId);
  return uploadWithProgress<SourceDocument>(`${V1}/documents/uploads`, form, {
    ...(opts.onProgress ? { onProgress: opts.onProgress } : {}),
    ...(opts.signal ? { signal: opts.signal } : {}),
  });
}

export function listDocuments(
  params: { limit?: number; offset?: number; org_id?: string },
  signal?: AbortSignal,
): Promise<Page<SourceDocument>> {
  return request<Page<SourceDocument>>(`${V1}/documents`, {
    query: {
      limit: params.limit ?? 20,
      offset: params.offset ?? 0,
      org_id: params.org_id,
    },
    signal,
  });
}

export function getDocument(documentId: string, signal?: AbortSignal): Promise<SourceDocument> {
  return request<SourceDocument>(`${V1}/documents/${encodeURIComponent(documentId)}`, { signal });
}

export function getProcessingStatus(
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentProcessingStatus> {
  return request<DocumentProcessingStatus>(
    `${V1}/documents/${encodeURIComponent(documentId)}/processing`,
    { signal },
  );
}

/**
 * `POST /v1/documents/{id}/ocr` -> `DocumentProcessingStatus` (202).
 *
 * Server decides eligibility: `preprocessed` / `review_required` / `failed`
 * may start recognition, an in-flight document returns its **existing** active
 * job (so repeats never create a duplicate), and everything else — including
 * `completed`, `deletion_pending` and other users' documents — is a 409/404.
 *
 * Recognition output stays provisional: the server labels it recognized
 * content, never verified truth.
 */
export function requestOcr(
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentProcessingStatus> {
  return request<DocumentProcessingStatus>(
    `${V1}/documents/${encodeURIComponent(documentId)}/ocr`,
    { method: "POST", signal },
  );
}

/**
 * `DELETE /v1/documents/{id}` -> `DocumentProcessingStatus` (202).
 *
 * Asynchronous and one-way: the server moves the document to
 * `deletion_pending` (no pipeline may start, a purge job cleans storage and
 * derived rows, a tombstone remains for audit) and only later reports `deleted`.
 * Repeating the call while a purge is in flight is idempotent; after the purge
 * finishes every read — including this one — returns 404.
 *
 * A 202 therefore means "deletion scheduled", NOT "deleted": callers must keep
 * polling until reads 404 before reporting success to a human.
 */
export function deleteDocument(
  documentId: string,
  signal?: AbortSignal,
): Promise<DocumentProcessingStatus> {
  return request<DocumentProcessingStatus>(
    `${V1}/documents/${encodeURIComponent(documentId)}`,
    { method: "DELETE", signal },
  );
}

export function listRegions(
  documentId: string,
  params: { limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<Page<OcrRegion>> {
  return request<Page<OcrRegion>>(`${V1}/documents/${encodeURIComponent(documentId)}/regions`, {
    query: { limit: params.limit ?? 200, offset: params.offset ?? 0 },
    signal,
  });
}

export function getSegmentationStatus(
  documentId: string,
  signal?: AbortSignal,
): Promise<SegmentationStatus> {
  return request<SegmentationStatus>(
    `${V1}/documents/${encodeURIComponent(documentId)}/segmentation`,
    { signal },
  );
}

/** Requires OCR regions to exist; returns 409 otherwise (handled as conflict). */
export function requestSegmentation(
  documentId: string,
  signal?: AbortSignal,
): Promise<SegmentationStatus> {
  return request<SegmentationStatus>(
    `${V1}/documents/${encodeURIComponent(documentId)}/segment`,
    { method: "POST", signal },
  );
}

export function listSegments(
  documentId: string,
  params: { limit?: number; offset?: number; segment_type?: string } = {},
  signal?: AbortSignal,
): Promise<Page<DocumentSegment>> {
  return request<Page<DocumentSegment>>(
    `${V1}/documents/${encodeURIComponent(documentId)}/segments`,
    {
      query: {
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
        segment_type: params.segment_type,
      },
      signal,
    },
  );
}

export function reviewSegment(
  documentId: string,
  segmentId: string,
  payload: SegmentReviewRequest,
  signal?: AbortSignal,
): Promise<SegmentReviewResponse> {
  return request<SegmentReviewResponse>(
    `${V1}/documents/${encodeURIComponent(documentId)}/segments/${encodeURIComponent(segmentId)}/review`,
    { method: "POST", body: payload, signal },
  );
}

/* -------------------------- knowledge ---------------------------- */

export function listTopics(
  params: { limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<Page<Topic>> {
  return request<Page<Topic>>(`${V1}/topics`, {
    query: { limit: params.limit ?? 20, offset: params.offset ?? 0 },
    signal,
  });
}

export function createTopic(
  payload: { name: string; description?: string },
  signal?: AbortSignal,
): Promise<Topic> {
  return request<Topic>(`${V1}/topics`, { method: "POST", body: payload, signal });
}

export function listGraphVersions(topicId: string, signal?: AbortSignal): Promise<GraphVersion[]> {
  return request<GraphVersion[]>(
    `${V1}/topics/${encodeURIComponent(topicId)}/graphs`,
    { signal },
  );
}

/* -------------------------- learning ----------------------------- */

/** `GET /v1/learners/me/reviews/due` -> list[ReviewResponse] */
export function listDueReviews(signal?: AbortSignal): Promise<ReviewItem[]> {
  return request<ReviewItem[]>(`${V1}/learners/me/reviews/due`, { signal });
}

/** `GET /v1/learners/me/mastery` -> list[MasteryResponse] */
export function listMastery(signal?: AbortSignal): Promise<MasteryEntry[]> {
  return request<MasteryEntry[]>(`${V1}/learners/me/mastery`, { signal });
}

/**
 * `POST /v1/learners/me/diagnoses` -> DiagnosisResponse.
 *
 * Runs the real prerequisite-gap diagnosis: competing hypotheses built from
 * the recorded graph relationships and the learner's mastery evidence. The
 * result is machine reasoning, never a verdict — the UI labels it as such.
 */
export function runDiagnosis(
  payload: { concept_id: string; org_id?: string },
  signal?: AbortSignal,
): Promise<Diagnosis> {
  return request<Diagnosis>(`${V1}/learners/me/diagnoses`, {
    method: "POST",
    body: payload,
    signal,
  });
}

/* -------------------------- profiles ----------------------------- */

/**
 * `GET /v1/me/profile`.
 *
 * NOTE: the profiles router is mounted **without** a sub-prefix
 * (`APIRouter(tags=["profiles"])` with routes like `/me/profile`), so the path
 * is `/v1/me/profile` — not `/v1/profiles/me/profile`.
 */
export function getMyProfile(signal?: AbortSignal): Promise<LearnerProfile> {
  return request<LearnerProfile>(`${V1}/me/profile`, { signal });
}

/* --------------------------- portfolio --------------------------- */

/** `GET /v1/me/portfolio/settings` */
export function getPortfolioSettings(signal?: AbortSignal): Promise<PortfolioSettings> {
  return request<PortfolioSettings>(`${V1}/me/portfolio/settings`, { signal });
}

/**
 * `PATCH /v1/me/portfolio/settings`.
 *
 * `is_public: true` is the explicit opt-in that makes the portfolio
 * readable by anyone holding the public id — the UI confirms before
 * sending it and never flips it silently.
 */
export function updatePortfolioSettings(
  payload: PortfolioSettingsUpdate,
  signal?: AbortSignal,
): Promise<PortfolioSettings> {
  return request<PortfolioSettings>(`${V1}/me/portfolio/settings`, {
    method: "PATCH",
    body: payload,
    signal,
  });
}

/** `GET /v1/me/portfolio/items` -> list[PortfolioItemResponse] */
export function listPortfolioItems(signal?: AbortSignal): Promise<PortfolioItem[]> {
  return request<PortfolioItem[]>(`${V1}/me/portfolio/items`, { signal });
}

/** `POST /v1/me/portfolio/items` -> PortfolioItemResponse (201) */
export function createPortfolioItem(
  payload: PortfolioItemCreate,
  signal?: AbortSignal,
): Promise<PortfolioItem> {
  return request<PortfolioItem>(`${V1}/me/portfolio/items`, {
    method: "POST",
    body: payload,
    signal,
  });
}

/** `GET /v1/portfolios/{public_id}` — anonymous read of a published profile. */
export function getPublicPortfolio(
  publicId: string,
  signal?: AbortSignal,
): Promise<PublicProfile> {
  return request<PublicProfile>(`${V1}/portfolios/${encodeURIComponent(publicId)}`, {
    anonymous: true,
    signal,
  });
}
