/**
 * Types mirroring the Unmist API response contracts.
 *
 * These are transcribed from `backend/app/schemas/*.py`. Field names and
 * value domains are taken from the source of truth; nothing here is invented.
 * If the backend changes, these types should change with it.
 */

/* ------------------------------------------------------------------ */
/* Envelopes                                                           */
/* ------------------------------------------------------------------ */

/** `backend/app/schemas/common.py` -> `Page[T]` */
export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

/** `backend/app/schemas/common.py` -> `HealthResponse` */
export interface HealthResponse {
  status: string;
  version: string;
  environment: string;
}

/** `backend/app/errors.py` -> `error_body()` */
export interface ApiErrorBody {
  error: {
    code?: string;
    message?: string;
    details?: unknown;
  };
}

/* ------------------------------------------------------------------ */
/* Auth (`backend/app/schemas/auth.py`)                                */
/* ------------------------------------------------------------------ */

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface OrgMembership {
  org_id: string;
  org_name: string;
  org_slug: string;
  role: string;
  joined_at: string;
}

export interface UserProfile {
  id: string;
  email: string;
  display_name: string | null;
  email_verified: boolean;
  is_active: boolean;
  created_at: string;
  organizations: OrgMembership[];
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  display_name?: string;
}

/* ------------------------------------------------------------------ */
/* Documents (`backend/app/schemas/documents.py`)                      */
/* ------------------------------------------------------------------ */

/**
 * `DocumentResponse`.
 *
 * NOTE: this payload does **not** carry segmentation state. Segmentation is a
 * separate axis exposed only by `GET /documents/{id}/segmentation`, so the
 * client must request it per document. See `segmentationAvailable` guards.
 */
export interface SourceDocument {
  id: string;
  owner_user_id: string | null;
  org_id: string | null;
  original_filename: string | null;
  media_type: string;
  content_hash: string;
  size_bytes: number;
  page_count: number;
  /** `DocumentProcessingState` value, or null for non-uploaded documents. */
  processing_state: string | null;
  error_category: string | null;
  error_detail: string | null;
  last_job_id: string | null;
  created_at: string;
  updated_at: string;
}

/** `DocumentProcessingStatus` — coarse lifecycle facts for polling. */
export interface DocumentProcessingStatus {
  id: string;
  processing_state: string | null;
  page_count: number;
  pages_processed: number;
  artifacts_ready: boolean;
  retry_available: boolean;
  error_category: string | null;
  error_detail: string | null;
  last_job_id: string | null;
}

/** `SegmentationStatusResponse` — the independent segmentation axis. */
export interface SegmentationStatus {
  id: string;
  segmentation_status: string;
  segmentation_version: string | null;
  segmentation_error_category: string | null;
  segmentation_error_detail: string | null;
  last_segment_job_id: string | null;
  segmentation_run: number;
  segment_available: boolean;
  total_segments: number;
  pending_review: number;
  reviewed: number;
}

/** `OcrRegionResponse` — line-level recognition result with provenance. */
export interface OcrRegion {
  id: string;
  page_id: string;
  region_type: string;
  reading_order: number | null;
  bbox_left: number;
  bbox_top: number;
  bbox_width: number;
  bbox_height: number;
  raw_text: string | null;
  text: string | null;
  confidence: number | null;
  provider: string;
  model_id: string;
  model_version: string | null;
  processing_version: string;
  review_status: string;
  created_at: string;
  updated_at: string;
}

/** `SegmentRegionRef` — page-local provenance for one contributing line. */
export interface SegmentRegionRef {
  region_id: string;
  page_number: number;
  position: number;
  bbox_left: number;
  bbox_top: number;
  bbox_width: number;
  bbox_height: number;
  text: string | null;
  review_status: string;
}

/** `SegmentResponse` — one question/answer candidate. */
export interface DocumentSegment {
  id: string;
  document_id: string;
  parent_id: string | null;
  segment_type: string;
  label: string | null;
  start_page: number;
  end_page: number;
  reading_order: number;
  bbox_left: number;
  bbox_top: number;
  bbox_width: number;
  bbox_height: number;
  text: string | null;
  effective_text: string | null;
  corrected_text: string | null;
  uncertainty: string;
  algorithm: string;
  algorithm_version: string;
  review_status: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  region_count: number;
  region_refs: SegmentRegionRef[];
  created_at: string;
  updated_at: string;
}

/** `SegmentReviewRequest` — action is exactly "confirm" | "correct". */
export interface SegmentReviewRequest {
  action: "confirm" | "correct";
  corrected_text?: string;
  reason?: string;
}

/** `SegmentReviewResponse` */
export interface SegmentReviewResponse {
  segment_id: string;
  revision: number;
  review_status: string;
  effective_text: string | null;
  reviewed_at: string;
}

/* ------------------------------------------------------------------ */
/* Knowledge (`backend/app/schemas/knowledge.py`)                      */
/* ------------------------------------------------------------------ */

export interface Topic {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  org_id: string | null;
  is_public: boolean;
  created_at: string;
  created_by: string | null;
}

/** `GraphVersionResponse` — counts only; the API exposes no node/edge list. */
export interface GraphVersion {
  id: string;
  version: number;
  status: string;
  concept_count: number;
  relationship_count: number;
  validation_report: Record<string, unknown>;
  created_at: string;
}

/* ------------------------------------------------------------------ */
/* Learning (`backend/app/schemas/learning.py`)                        */
/* ------------------------------------------------------------------ */

export interface Mastery {
  concept_id?: string;
  concept_name?: string;
  value?: number;
  [key: string]: unknown;
}

/**
 * `GET /v1/learners/me/mastery` (MasteryResponse).
 *
 * `mastery_probability` is a *provisional model estimate*, never an
 * established fact — the UI must label it with `model_version` and
 * `evidence_count` wherever it is shown.
 */
export interface MasteryEntry {
  concept_id: string;
  mastery_probability: number;
  evidence_count: number;
  state: string;
  model_version: string;
  last_activity_at: string | null;
}

/** One prerequisite hypothesis from `build_hypotheses` (machine-generated). */
export interface DiagnosisHypothesis {
  concept_id: string;
  stable_key: string;
  canonical_name: string;
  mastery_probability: number;
  evidence_count: number;
  likely_gap: boolean;
  information_gain: number;
}

/** `POST /v1/learners/me/diagnoses` (DiagnosisResponse). */
export interface Diagnosis {
  id: string;
  concept_id: string;
  status: string;
  hypotheses: DiagnosisHypothesis[];
  selected_question_id: string | null;
  conclusion: string | null;
  evidence_summary: string | null;
  created_at: string;
}

export interface ReviewItem {
  id?: string;
  schedule_id?: string;
  [key: string]: unknown;
}

/* ------------------------------------------------------------------ */
/* Profiles                                                            */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* Profiles (`backend/app/schemas/profile.py` + `services/profiles`)   */
/* ------------------------------------------------------------------ */

/** One entry of `ProfileResponse.mastery` (see services/profiles/profile.py). */
export interface ProfileMasteryEntry {
  concept_id: string;
  canonical_name: string;
  topic_id: string | null;
  mastery_probability: number;
  evidence_count: number;
  state: string;
  model_version: string;
  last_activity_at: string | null;
}

/** One entry of `ProfileResponse.retention`. */
export interface ProfileRetentionEntry {
  concept_id: string;
  canonical_name: string;
  status: string;
  due_at: string | null;
  interval_days: number;
  lapses: number;
  timezone: string | null;
}

/** One entry of `ProfileResponse.assessment_history`. */
export interface ProfileAttemptEntry {
  attempt_id: string;
  question_id: string;
  status: string;
  score: number | null;
  max_score: number | null;
  rubric_version: string | null;
  evaluator_version: string | null;
  submitted_at: string | null;
}

/** One entry of `ProfileResponse.diagnoses`. */
export interface ProfileDiagnosisEntry {
  diagnosis_id: string;
  concept_id: string;
  status: string;
  conclusion: string | null;
  created_at: string;
}

/** `GET /v1/me/profile` (profiles router is mounted without a sub-prefix) */
export interface LearnerProfile {
  learner_id: string;
  org_id: string | null;
  mastery: ProfileMasteryEntry[];
  retention: ProfileRetentionEntry[];
  prerequisite_gaps: ProfileMasteryEntry[];
  assessment_history: ProfileAttemptEntry[];
  diagnoses: ProfileDiagnosisEntry[];
  topics: Array<{ topic_id: string; name: string; slug: string }>;
}

/* ------------------------------------------------------------------ */
/* Portfolio (`backend/app/schemas/profile.py`)                        */
/* ------------------------------------------------------------------ */

/** `GET|PATCH /v1/me/portfolio/settings` — publishing is explicit opt-in. */
export interface PortfolioSettings {
  public_id: string;
  is_public: boolean;
  headline: string | null;
  bio: string | null;
  show_assessed_only: boolean;
  published_at: string | null;
}

export interface PortfolioSettingsUpdate {
  is_public?: boolean;
  headline?: string;
  bio?: string;
  show_assessed_only?: boolean;
}

/** `GET|POST /v1/me/portfolio/items` */
export interface PortfolioItem {
  id: string;
  item_type: string;
  title: string;
  evidence_summary: string | null;
  is_self_reported: boolean;
  visibility: string;
  score: number | null;
  max_score: number | null;
}

export interface PortfolioItemCreate {
  item_type: "concept" | "skill" | "project" | "assessment" | "certificate";
  title: string;
  concept_id?: string;
  evidence_summary?: string;
  is_self_reported?: boolean;
  visibility?: "private" | "public";
  score?: number;
  max_score?: number;
}

/** One item as exposed publicly (never carries private source content). */
export interface PublicPortfolioItem {
  item_type: string;
  title: string;
  evidence_summary: string | null;
  is_self_reported: boolean;
  verification_note: string;
  score: number | null;
  max_score: number | null;
}

/** `GET /v1/portfolios/{public_id}` — authorized public read only. */
export interface PublicProfile {
  public_id: string;
  headline: string | null;
  bio: string | null;
  items: PublicPortfolioItem[];
  limitations: string;
}
