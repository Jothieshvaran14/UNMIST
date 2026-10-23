/**
 * Document detail and processing workspace.
 *
 * Shows the real lifecycle as discrete stages (never a fabricated percentage),
 * keeps OCR and segmentation on separate axes, and polls conservatively:
 *   - 3s interval, only while a stage is actually in flight
 *   - polling stops at every terminal state
 *   - outstanding requests abort on unmount and never overlap
 *   - a transient error keeps the last known status and offers a manual refresh
 */

import { useCallback, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  CheckCircle2,
  CircleDashed,
  FileStack,
  Layers,
  Loader2,
  PenLine,
  RefreshCw,
  ScanText,
  Split,
  TriangleAlert,
} from "lucide-react";
import { useAsyncData, usePolling } from "@/hooks/useAsyncData";
import { ProcessingPill, SegmentationPill, processingInfo, segmentationInfo } from "@/components/status";
import {
  Alert,
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadingRow,
  toUserMessage,
  useToast,
} from "@/components/ui";
import { ApiClientError } from "@/services/http";
import * as api from "@/services/api";
import type { DocumentProcessingStatus } from "@/types/api";

/* ------------------------------------------------------------------ */
/* Stage derivation                                                    */
/* ------------------------------------------------------------------ */

type StageKind = "done" | "active" | "error" | "review" | "pending" | "unavailable";
type StageKey = "upload" | "preprocess" | "ocr" | "segmentation" | "review";

interface Stage {
  key: StageKey;
  title: string;
  description: string;
  kind: StageKind;
}

/** States after which page preparation definitely finished. */
const PREP_DONE = new Set(["preprocessed", "ocr_processing", "review_required", "completed"]);
const OCR_DONE = new Set(["completed"]);
const OCR_ACTIVE = new Set(["ocr_processing"]);

function buildStages(
  state: string | null,
  processing: DocumentProcessingStatus | null,
  segmentationStatus: string | null,
  pendingReview: number,
  reviewed: number,
  totalSegments: number,
): Stage[] {
  const prepKind: StageKind =
    state === "queued" || state === "preprocessing"
      ? "active"
      : state === "failed" && !processing?.artifacts_ready
        ? "error"
        : state && PREP_DONE.has(state)
          ? "done"
          : state === "cancelled" || state === "deleted"
            ? "unavailable"
            : "pending";

  let ocrKind: StageKind;
  if (state === "failed") ocrKind = processing?.artifacts_ready ? "error" : "unavailable";
  else if (state && OCR_ACTIVE.has(state)) ocrKind = "active";
  else if (state === "review_required") ocrKind = "review";
  else if (state && OCR_DONE.has(state)) ocrKind = "done";
  else if (state === "preprocessed") ocrKind = "pending";
  else if (state === "cancelled" || state === "deleted") ocrKind = "unavailable";
  else ocrKind = "pending";

  const segInfo = segmentationInfo(segmentationStatus ?? undefined);
  let segKind: StageKind;
  switch (segmentationStatus) {
    case "completed":
      segKind = "done";
      break;
    case "partial":
      segKind = "review";
      break;
    case "failed":
      segKind = "error";
      break;
    case "scheduled":
    case "processing":
      segKind = "active";
      break;
    case "cancelled":
      segKind = "unavailable";
      break;
    case "not_run":
      segKind = "pending";
      break;
    default:
      segKind = "pending";
  }

  let reviewKind: StageKind;
  if (segmentationStatus === "completed" || segmentationStatus === "partial") {
    if (totalSegments === 0) reviewKind = "pending";
    else if (pendingReview > 0) reviewKind = "review";
    else reviewKind = "done";
  } else {
    reviewKind = "pending";
  }

  const prepared =
    processing && state && PREP_DONE.has(state)
      ? `${processing.pages_processed} of ${processing.page_count} pages prepared`
      : "Rasterize and normalize every page into working images.";

  const reviewedLabel =
    totalSegments > 0
      ? `${reviewed} of ${totalSegments} segments reviewed`
      : "Confirm or correct each extracted segment.";

  return [
    {
      key: "upload",
      title: "Upload",
      description: "Stored privately and scheduled for processing.",
      kind: state === "deleted" ? "unavailable" : "done",
    },
    {
      key: "preprocess",
      title: "Preprocessing",
      description: prepared,
      kind: prepKind,
    },
    {
      key: "ocr",
      title: "Text recognition",
      description:
        ocrKind === "active"
          ? "Recognizing line-level regions on each prepared page."
          : ocrKind === "done"
            ? "Every page produced recognized regions."
            : ocrKind === "review"
              ? "Recognition finished for some pages; a human should look."
              : ocrKind === "error"
                ? "Recognition stopped with an error."
                : ocrKind === "unavailable"
                  ? "Not applicable in the document's current state."
                  : "Waiting for page preparation to finish.",
      kind: ocrKind,
    },
    {
      key: "segmentation",
      title: "Segmentation",
      description:
        segKind === "done"
          ? `${totalSegments} segment${totalSegments === 1 ? "" : "s"} extracted.`
          : segInfo.hint ?? "Group recognized regions into question and answer candidates.",
      kind: segKind,
    },
    {
      key: "review",
      title: "Human review",
      description: reviewedLabel,
      kind: reviewKind,
    },
  ];
}

function StageIcon({ kind }: { kind: StageKind }) {
  if (kind === "done") return <CheckCircle2 />;
  if (kind === "active") return <Loader2 className="icon-spin" />;
  if (kind === "error") return <TriangleAlert />;
  if (kind === "review") return <PenLine />;
  if (kind === "unavailable") return <CircleDashed />;
  return <CircleDashed />;
}

function stageClass(kind: StageKind): string {
  if (kind === "done") return "tl-item is-done";
  if (kind === "active") return "tl-item is-active";
  if (kind === "error") return "tl-item is-error";
  if (kind === "review") return "tl-item is-review";
  return "tl-item";
}

const KIND_LABEL: Record<StageKind, string> = {
  done: "Complete",
  active: "In progress",
  error: "Failed",
  review: "Needs review",
  pending: "Pending",
  unavailable: "Unavailable",
};

/* ------------------------------------------------------------------ */

export function DocumentDetailPage() {
  const { documentId = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();

  const [segmenting, setSegmenting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const doc = useAsyncData((signal) => api.getDocument(documentId, signal), [documentId]);
  const processing = useAsyncData(
    (signal) => api.getProcessingStatus(documentId, signal),
    [documentId],
  );
  const segStatus = useAsyncData(
    (signal) => api.getSegmentationStatus(documentId, signal),
    [documentId],
  );

  const state = doc.data?.processing_state ?? processing.data?.processing_state ?? null;
  const segState = segStatus.data?.segmentation_status ?? null;

  const refreshAll = useCallback(() => {
    doc.reload();
    processing.reload();
    segStatus.reload();
  }, [doc, processing, segStatus]);

  const processingActive = processingInfo(state).active;
  const segmentationActive = segState === "scheduled" || segState === "processing";

  // Conservative polling: only while a stage is genuinely in flight.
  usePolling(async () => {
    processing.reload();
    doc.reload();
    segStatus.reload();
    return undefined;
  }, 3000, processingActive || segmentationActive);

  const stages = useMemo(
    () =>
      buildStages(
        state,
        processing.data,
        segState,
        segStatus.data?.pending_review ?? 0,
        segStatus.data?.reviewed ?? 0,
        segStatus.data?.total_segments ?? 0,
      ),
    [state, processing.data, segState, segStatus.data],
  );

  const segments = useAsyncData(
    (signal) => api.listSegments(documentId, { limit: 200 }, signal),
    [documentId],
    { enabled: (segStatus.data?.total_segments ?? 0) > 0 },
  );

  // Line-level OCR output. Always requested for an existing document: an empty
  // result is the honest answer before recognition has run.
  const regions = useAsyncData(
    (signal) => api.listRegions(documentId, { limit: 60 }, signal),
    [documentId],
  );

  const canSegment = Boolean(segStatus.data?.segment_available);

  async function startSegmentation() {
    setSegmenting(true);
    setActionError(null);
    try {
      await api.requestSegmentation(documentId);
      toast.success("Segmentation requested", "A durable segmentation job was scheduled.");
      refreshAll();
    } catch (err) {
      const message =
        err instanceof ApiClientError && err.status === 409
          ? "Segmentation cannot start in the document's current state. Recognition must finish first."
          : toUserMessage(err);
      setActionError(message);
    } finally {
      setSegmenting(false);
    }
  }

  if (doc.initialLoading) return <LoadingRow label="Loading document…" />;
  if (doc.error && !doc.data) {
    return (
      <>
        <BackLink />
        <div className="card card--pad">
          <ErrorState error={doc.error} onRetry={doc.reload} />
        </div>
      </>
    );
  }
  if (!doc.data) {
    return (
      <>
        <BackLink />
        <div className="card card--pad">
          <EmptyState title="Document not found" description="It may have been deleted." />
        </div>
      </>
    );
  }

  const document = doc.data;
  const failed = document.processing_state === "failed";
  const segFailed = segState === "failed";
  const seg = segStatus.data;
  // Recognition has produced (or attempted) regions in these states only.
  const ocrHasRun =
    document.processing_state === "completed" ||
    document.processing_state === "review_required" ||
    document.processing_state === "ocr_processing";

  return (
    <>
      <BackLink />

      <div className="page-head">
        <div className="page-head__title">
          <span className="page-head__eyebrow">Document</span>
          <h1 className="truncate" title={document.original_filename ?? undefined}>
            {document.original_filename || "Untitled document"}
          </h1>
          <div className="row gap-2 wrap">
            <ProcessingPill state={document.processing_state} />
            <SegmentationPill status={segState} />
            <Badge tone="neutral">{document.media_type}</Badge>
          </div>
        </div>
        <div className="page-head__actions">
          <Button
            variant="ghost"
            onClick={refreshAll}
            icon={<RefreshCw />}
            disabled={doc.refreshing || processing.refreshing}
            title="Refresh status"
          >
            Refresh
          </Button>
          {canSegment && (
            <Button variant="primary" onClick={startSegmentation} loading={segmenting} icon={<Split />}>
              Segment document
            </Button>
          )}
          {(seg?.total_segments ?? 0) > 0 && (
            <Button variant="secondary" onClick={() => navigate(`/documents/${document.id}/segments`)}>
              Review segments ({seg?.total_segments})
            </Button>
          )}
        </div>
      </div>

      {actionError && (
        <div style={{ marginBottom: "var(--space-4)" }}>
          <Alert tone="danger" title="Could not start segmentation">{actionError}</Alert>
        </div>
      )}

      {failed && (
        <div style={{ marginBottom: "var(--space-4)" }}>
          <Alert tone="danger" title="Processing failed">
            {document.error_category && (
              <>
                Category <code>{document.error_category}</code>.{" "}
              </>
            )}
            {document.error_detail || "No further detail was recorded."}
            {processing.data?.retry_available && (
              <div className="mt-2 text-xs">
                The backend marks this document retryable, but there is no retry endpoint exposed
                to the API yet — re-uploading the same file creates a new record.
              </div>
            )}
          </Alert>
        </div>
      )}

      {segFailed && (
        <div style={{ marginBottom: "var(--space-4)" }}>
          <Alert tone="danger" title="Segmentation failed">
            {seg?.segmentation_error_category && (
              <>
                Category <code>{seg.segmentation_error_category}</code>.{" "}
              </>
            )}
            {seg?.segmentation_error_detail || "No further detail was recorded."}
          </Alert>
        </div>
      )}

      <div className="split">
        <div className="stack gap-5">
          <Card title="Processing timeline" icon={<Loader2 />}>
            <ol className="timeline">
              {stages.map((stage) => (
                <li className={stageClass(stage.kind)} key={stage.key}>
                  <span className="tl-node" aria-hidden="true">
                    <StageIcon kind={stage.kind} />
                  </span>
                  <div className="tl-body">
                    <div className="tl-title">
                      {stage.title}
                      <Badge
                        tone={
                          stage.kind === "done"
                            ? "success"
                            : stage.kind === "active"
                              ? "info"
                              : stage.kind === "error"
                                ? "danger"
                                : stage.kind === "review"
                                  ? "warning"
                                  : "neutral"
                        }
                      >
                        {KIND_LABEL[stage.kind]}
                      </Badge>
                    </div>
                    <div className="tl-desc">{stage.description}</div>
                  </div>
                </li>
              ))}
            </ol>
            {(processingActive || segmentationActive) && (
              <div className="mt-4 text-xs text-muted row gap-2">
                <span className="spinner" style={{ width: 12, height: 12 }} aria-hidden="true" />
                Polling every 3s while a stage is in flight — stops automatically.
              </div>
            )}
          </Card>

          <Card title="Document metadata" icon={<FileStack />}>
            <dl className="dl">
              <dt>Document id</dt>
              <dd className="mono">{document.id}</dd>
              <dt>Media type</dt>
              <dd>{document.media_type}</dd>
              <dt>Size</dt>
              <dd>{document.size_bytes.toLocaleString()} bytes</dd>
              <dt>Pages</dt>
              <dd>
                {document.page_count}
                {processing.data
                  ? ` · ${processing.data.pages_processed} processed`
                  : ""}
              </dd>
              <dt>Uploaded</dt>
              <dd>{new Date(document.created_at).toLocaleString()}</dd>
              <dt>Updated</dt>
              <dd>{new Date(document.updated_at).toLocaleString()}</dd>
              <dt>Content hash</dt>
              <dd className="mono" title={document.content_hash}>
                {document.content_hash.slice(0, 16)}…
              </dd>
            </dl>
            <p className="text-xs text-muted mt-4">
              Storage keys and filesystem paths are never returned by the API and are not shown
              here.
            </p>
          </Card>
        </div>

        <div className="stack gap-5">
          <div className="grid grid--3">
            <div className="stat">
              <div className="stat__label">
                <ScanText />
                Recognition
              </div>
              <div className="stat__value stat__value--sm">
                {processingInfo(state).label}
              </div>
              <div className="stat__note">
                {processing.data
                  ? `${processing.data.pages_processed} of ${processing.data.page_count} pages`
                  : "Status unavailable"}
              </div>
            </div>
            <div className="stat">
              <div className="stat__label">
                <Split />
                Segmentation
              </div>
              <div className="stat__value stat__value--sm">
                {segmentationInfo(segState ?? undefined).label}
              </div>
              <div className="stat__note">
                {seg ? `${seg.total_segments} segments · ${seg.pending_review} pending review` : "Status unavailable"}
              </div>
            </div>
            <div className="stat">
              <div className="stat__label">
                <PenLine />
                Review
              </div>
              <div className="stat__value stat__value--sm">
                {seg && seg.total_segments > 0
                  ? `${seg.reviewed}/${seg.total_segments}`
                  : "—"}
              </div>
              <div className="stat__note">
                {seg && seg.total_segments > 0 ? "segments reviewed" : "No segments yet"}
              </div>
            </div>
          </div>

          <Card
            title="Question and answer segments"
            icon={<Layers />}
            action={
              (seg?.total_segments ?? 0) > 0 ? (
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => navigate(`/documents/${document.id}/segments`)}
                >
                  Open workspace
                </Button>
              ) : undefined
            }
          >
            {segStatus.initialLoading ? (
              <LoadingRow label="Loading segments…" />
            ) : segments.error ? (
              <ErrorState error={segments.error} onRetry={segments.reload} compact />
            ) : (seg?.total_segments ?? 0) === 0 ? (
              <EmptyState
                icon={<Split />}
                title="No segments yet"
                description={
                  segState === "completed"
                    ? "Segmentation reported no segments for this document."
                    : "Segments appear once segmentation runs over recognized regions. Request segmentation when the document has completed recognition."
                }
              />
            ) : (
              <ul className="stack gap-2">
                {(segments.data?.items ?? []).slice(0, 6).map((segment, index) => (
                  <li key={segment.id}>
                    <button
                      className="seg-item"
                      style={{ width: "100%", textAlign: "left", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)" }}
                      onClick={() => navigate(`/documents/${document.id}/segments?segment=${segment.id}`)}
                    >
                      <span className="seg-item__head">
                        <span className="seg-item__index">#{index + 1}</span>
                        <span className="seg-item__label">
                          {segment.label || segment.segment_type}
                        </span>
                        <span className="seg-item__type">
                          <Badge tone="neutral">{segment.segment_type}</Badge>
                        </span>
                      </span>
                      <span className="seg-item__text">
                        {segment.effective_text || segment.text || "(no text)"}
                      </span>
                    </button>
                  </li>
                ))}
                {(seg?.total_segments ?? 0) > 6 && (
                  <li className="text-xs text-muted" style={{ padding: "var(--space-2)" }}>
                    …and {(seg!.total_segments - 6).toLocaleString()} more in the workspace.
                  </li>
                )}
              </ul>
            )}
          </Card>

          <Card
            title={`Recognized regions${regions.data ? ` (${regions.data.total})` : ""}`}
            icon={<ScanText />}
            action={
              (seg?.total_segments ?? 0) > 0 ? (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => navigate(`/documents/${document.id}/segments`)}
                >
                  Open segment workspace
                </Button>
              ) : undefined
            }
          >
            {regions.initialLoading ? (
              <LoadingRow label="Loading regions…" />
            ) : regions.error ? (
              <ErrorState error={regions.error} onRetry={regions.reload} compact />
            ) : (regions.data?.items.length ?? 0) === 0 ? (
              <EmptyState
                icon={<ScanText />}
                title="No recognized regions yet"
                description={
                  ocrHasRun
                    ? "The server returned an empty region list for this document."
                    : "Line-level regions appear here once text recognition runs. Nothing is rendered before then."
                }
              />
            ) : (
              <div className="stack gap-3">
                <ul className="stack gap-2">
                  {(regions.data?.items ?? []).slice(0, 12).map((region) => (
                    <li className="region-ref" key={region.id}>
                      <span className="region-ref__page">
                        {region.reading_order !== null ? `#${region.reading_order}` : region.region_type}
                      </span>
                      <span className="grow" style={{ minWidth: 0 }}>
                        <span className="region-ref__text">
                          {region.text || region.raw_text || "(no text on this line)"}
                        </span>
                        <span className="region-ref__geom">
                          {region.bbox_left},{region.bbox_top} {region.bbox_width}×{region.bbox_height} ·{" "}
                          {region.provider}/{region.model_id}
                          {region.model_version ? `@${region.model_version}` : ""} ·{" "}
                          {region.processing_version}
                          {region.confidence !== null && region.confidence !== undefined
                            ? ` · confidence ${region.confidence} (server-provided)`
                            : ""}
                        </span>
                      </span>
                      <Badge tone={region.review_status === "confirmed" ? "success" : "neutral"}>
                        {region.review_status}
                      </Badge>
                    </li>
                  ))}
                </ul>
                <p className="text-xs text-muted">
                  {regions.data && regions.data.items.length < regions.data.total
                    ? `Showing ${regions.data.items.length} of ${regions.data.total} regions. `
                    : ""}
                  Coordinates are page-local pixels referenced by{" "}
                  <code>{(regions.data?.items[0]?.page_id ?? "").slice(0, 8) || "page"}</code>.
                  Confidence is shown only when the server supplies one.
                </p>
              </div>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

function BackLink() {
  return (
    <div style={{ marginBottom: "var(--space-4)" }}>
      <Link
        to="/documents"
        className="row gap-2 text-sm"
        style={{ color: "var(--text-secondary)" }}
      >
        <ArrowLeft style={{ width: 15, height: 15 }} aria-hidden="true" />
        Back to documents
      </Link>
    </div>
  );
}
