/**
 * Question-and-answer segmentation workspace.
 *
 * Split-pane on desktop (segment list | segment detail with evidence), stacked
 * on mobile. Every piece of text shown is what the server returned: assembled
 * region text, or the reviewer's correction when one exists. Uncertainty is
 * rendered as the backend's *category* — never as a percentage, because the
 * API deliberately does not provide one.
 *
 * Review mutations call the real endpoint. A structural confirmation/correction
 * is explicitly distinguished from an assessment of answer correctness, which
 * is out of scope.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  Check,
  CornerDownRight,
  Eye,
  FileStack,
  MessageSquareText,
  PencilLine,
  RefreshCw,
  Save,
  Split,
  Undo2,
} from "lucide-react";
import { useAsyncData } from "@/hooks/useAsyncData";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  Field,
  LoadingRow,
  Modal,
  Alert,
  toUserMessage,
  useToast,
} from "@/components/ui";
import {
  TypeBadge,
  reviewInfo,
  segmentTypeLabel,
  uncertaintyInfo,
} from "@/components/status";
import { ApiClientError } from "@/services/http";
import * as api from "@/services/api";
import type { SegmentReviewResponse } from "@/types/api";

export function SegmentWorkspacePage() {
  const { documentId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const toast = useToast();

  const [selectedId, setSelectedId] = useState<string | null>(params.get("segment"));
  const [confirming, setConfirming] = useState(false);
  const [correcting, setCorrecting] = useState(false);
  const [correction, setCorrection] = useState("");
  const [busy, setBusy] = useState(false);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [lastReview, setLastReview] = useState<SegmentReviewResponse | null>(null);

  const segments = useAsyncData(
    (signal) => api.listSegments(documentId, { limit: 200 }, signal),
    [documentId],
  );
  const document = useAsyncData((signal) => api.getDocument(documentId, signal), [documentId]);

  const items = useMemo(() => segments.data?.items ?? [], [segments.data]);

  // Keep the selection valid as data arrives (and support ?segment= deep links).
  useEffect(() => {
    if (items.length === 0) return;
    if (!selectedId || !items.some((segment) => segment.id === selectedId)) {
      const first = items[0];
      setSelectedId(first.id);
      const next = new URLSearchParams(params);
      next.set("segment", first.id);
      setParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, selectedId]);

  const selected = useMemo(
    () => items.find((segment) => segment.id === selectedId) ?? null,
    [items, selectedId],
  );

  const children = useMemo(
    () => items.filter((segment) => segment.parent_id === selected?.id),
    [items, selected],
  );
  const parent = useMemo(
    () => (selected?.parent_id ? items.find((segment) => segment.id === selected.parent_id) ?? null : null),
    [items, selected],
  );

  const select = useCallback(
    (id: string) => {
      setSelectedId(id);
      setReviewError(null);
      setLastReview(null);
      const next = new URLSearchParams(params);
      next.set("segment", id);
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  /** Refresh only the affected segment, falling back to a list reload. */
  const applyReview = useCallback(
    (response: SegmentReviewResponse) => {
      setLastReview(response);
      segments.setData((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          items: prev.items.map((segment) =>
            segment.id === response.segment_id
              ? {
                  ...segment,
                  review_status: response.review_status,
                  effective_text: response.effective_text,
                  corrected_text:
                    response.review_status === "corrected"
                      ? response.effective_text
                      : segment.corrected_text,
                  reviewed_at: response.reviewed_at,
                }
              : segment,
          ),
        };
      });
      toast.success(
        "Review recorded",
        `Revision ${response.revision} · status ${response.review_status}.`,
      );
    },
    [segments, toast],
  );

  async function submitReview(action: "confirm" | "correct") {
    if (!selected) return;
    setBusy(true);
    setReviewError(null);
    try {
      const response = await api.reviewSegment(documentId, selected.id, {
        action,
        ...(action === "correct" ? { corrected_text: correction } : {}),
      });
      applyReview(response);
      setConfirming(false);
      setCorrecting(false);
      setCorrection("");
    } catch (err) {
      if (err instanceof ApiClientError && err.status === 409) {
        setReviewError(
          "This segment cannot be reviewed in the document's current state.",
        );
      } else if (err instanceof ApiClientError && err.status === 404) {
        setReviewError("That segment no longer exists. Reloading the list.");
        segments.reload();
      } else {
        setReviewError(toUserMessage(err));
      }
    } finally {
      setBusy(false);
    }
  }

  function openCorrection() {
    if (!selected) return;
    setCorrection(selected.effective_text || selected.text || "");
    setReviewError(null);
    setCorrecting(true);
  }

  if (segments.initialLoading) return <LoadingRow label="Loading segments…" />;

  if (segments.error && items.length === 0) {
    return (
      <>
        <BackLink documentId={documentId} />
        <div className="card card--pad">
          <ErrorState error={segments.error} onRetry={segments.reload} />
        </div>
      </>
    );
  }

  if (items.length === 0) {
    return (
      <>
        <BackLink documentId={documentId} />
        <div className="card card--pad">
          <EmptyState
            icon={<Split />}
            title="No segments for this document"
            description="Segments appear after segmentation runs over recognized regions. Open the document and request segmentation once recognition has completed."
            action={
              <Link to={`/documents/${documentId}`}>
                <Button variant="primary">Back to document</Button>
              </Link>
            }
          />
        </div>
      </>
    );
  }

  const uncertainty = selected ? uncertaintyInfo(selected.uncertainty) : null;
  const review = selected ? reviewInfo(selected.review_status) : null;

  return (
    <>
      <BackLink documentId={documentId} />

      <div className="page-head">
        <div className="page-head__title">
          <span className="page-head__eyebrow">Review workspace</span>
          <h1>{document.data?.original_filename || "Segments"}</h1>
          <p className="page-head__desc">
            Candidate structure extracted from recognized regions. Each segment lists the exact
            regions it was assembled from, with page numbers and page-local coordinates.
          </p>
        </div>
        <div className="page-head__actions">
          <Button
            variant="ghost"
            icon={<RefreshCw />}
            onClick={segments.reload}
            disabled={segments.refreshing}
          >
            Refresh
          </Button>
          <Link to={`/documents/${documentId}`}>
            <Button variant="secondary" icon={<FileStack />}>
              Document detail
            </Button>
          </Link>
        </div>
      </div>

      <div className="split">
        <Card
          title={`Segments (${segments.data?.total ?? items.length})`}
          icon={<Split />}
          pad={false}
          action={
            <Badge tone="neutral">
              {items.filter((item) => item.review_status !== "pending").length} reviewed
            </Badge>
          }
        >
          <ul className="seg-list">
            {items.map((segment, index) => {
              const segReview = reviewInfo(segment.review_status);
              const segUncertainty = uncertaintyInfo(segment.uncertainty);
              return (
                <li key={segment.id}>
                  <button
                    className={`seg-item ${segment.id === selectedId ? "is-selected" : ""}`.trim()}
                    style={{ width: "100%", textAlign: "left", background: "none" }}
                    onClick={() => select(segment.id)}
                    aria-current={segment.id === selectedId ? "true" : undefined}
                  >
                    <span className="seg-item__head">
                      <span className="seg-item__index">{String(index + 1).padStart(2, "0")}</span>
                      <span className="seg-item__label">
                        {segment.label || segmentTypeLabel(segment.segment_type)}
                      </span>
                      <span className="seg-item__type">
                        <TypeBadge type={segment.segment_type} />
                      </span>
                    </span>
                    <span className="row gap-2 wrap">
                      <Badge tone={segReview.tone}>{segReview.label}</Badge>
                      {segUncertainty && <Badge tone={segUncertainty.tone}>{segUncertainty.label}</Badge>}
                      {segment.corrected_text && <Badge tone="accent">Corrected</Badge>}
                    </span>
                    <span className="seg-item__text">
                      {segment.effective_text || segment.text || "(no text)"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </Card>

        <div className="stack gap-5">
          {selected ? (
            <>
              <Card
                title={
                  selected.label
                    ? `${selected.label} · ${segmentTypeLabel(selected.segment_type)}`
                    : segmentTypeLabel(selected.segment_type)
                }
                icon={<MessageSquareText />}
                action={
                  <div className="row gap-2">
                    {review && <Badge tone={review.tone}>{review.label}</Badge>}
                    {uncertainty && <Badge tone={uncertainty.tone}>{uncertainty.label}</Badge>}
                  </div>
                }
                footer={
                  <div className="row gap-3 wrap">
                    <Button
                      variant="primary"
                      icon={<Check />}
                      onClick={() => {
                        setReviewError(null);
                        setConfirming(true);
                      }}
                      disabled={busy}
                    >
                      Confirm structure
                    </Button>
                    <Button variant="secondary" icon={<PencilLine />} onClick={openCorrection} disabled={busy}>
                      Correct text
                    </Button>
                    <span className="text-xs text-muted grow" style={{ textAlign: "right" }}>
                      Records structural review — not an assessment of answer correctness.
                    </span>
                  </div>
                }
              >
                <div className="stack gap-4">
                  {reviewError && (
                    <Alert tone="danger" title="Review not recorded">{reviewError}</Alert>
                  )}

                  {lastReview && !reviewError && (
                    <Alert tone="success" title="Server confirmed the review">
                      Revision {lastReview.revision} · status <strong>{lastReview.review_status}</strong>
                      {" · "}
                      {new Date(lastReview.reviewed_at).toLocaleString()}
                    </Alert>
                  )}

                  <div className="row gap-2 wrap text-xs text-muted">
                    <span>
                      Pages {selected.start_page}
                      {selected.end_page !== selected.start_page ? `–${selected.end_page}` : ""}
                    </span>
                    <span aria-hidden="true">·</span>
                    <span>Reading order {selected.reading_order}</span>
                    <span aria-hidden="true">·</span>
                    <span>
                      {selected.region_count} source region{selected.region_count === 1 ? "" : "s"}
                    </span>
                    <span aria-hidden="true">·</span>
                    <span className="mono">
                      {selected.algorithm} v{selected.algorithm_version}
                    </span>
                  </div>

                  {selected.corrected_text ? (
                    <div className="stack gap-2">
                      <div className="field__label row gap-2">
                        <PencilLine style={{ width: 14, height: 14 }} aria-hidden="true" />
                        Reviewed text (replaces the derived text)
                      </div>
                      <div className="seg-detail__text seg-detail__corrected">
                        {selected.effective_text}
                      </div>
                      {selected.reviewed_at && (
                        <div className="text-xs text-muted">
                          Reviewed {new Date(selected.reviewed_at).toLocaleString()}
                        </div>
                      )}
                      <details>
                        <summary className="text-xs text-muted" style={{ cursor: "pointer" }}>
                          Show the original derived text
                        </summary>
                        <div className="seg-detail__text mt-2">{selected.text}</div>
                      </details>
                    </div>
                  ) : (
                    <div className="stack gap-2">
                      <div className="field__label">Segment text (derived from regions)</div>
                      <div className="seg-detail__text">
                        {selected.effective_text || selected.text || "(no text)"}
                      </div>
                    </div>
                  )}

                  {(parent || children.length > 0) && (
                    <div className="stack gap-2">
                      <div className="field__label">Hierarchy</div>
                      {parent && (
                        <button
                          className="region-ref"
                          style={{ width: "100%", textAlign: "left", cursor: "pointer" }}
                          onClick={() => select(parent.id)}
                        >
                          <CornerDownRight style={{ width: 14, height: 14 }} aria-hidden="true" />
                          <span className="grow">
                            <strong>Parent:</strong> {parent.label || segmentTypeLabel(parent.segment_type)}
                            <div className="region-ref__text clamp-2">
                              {parent.effective_text || parent.text || "(no text)"}
                            </div>
                          </span>
                        </button>
                      )}
                      {children.map((child) => (
                        <button
                          key={child.id}
                          className="region-ref"
                          style={{ width: "100%", textAlign: "left", cursor: "pointer" }}
                          onClick={() => select(child.id)}
                        >
                          <CornerDownRight style={{ width: 14, height: 14 }} aria-hidden="true" />
                          <span className="grow">
                            <strong>Child:</strong> {child.label || segmentTypeLabel(child.segment_type)}
                            <div className="region-ref__text clamp-2">
                              {child.effective_text || child.text || "(no text)"}
                            </div>
                          </span>
                        </button>
                      ))}
                    </div>
                  )}

                  <div className="stack gap-2">
                    <div className="field__label row gap-2">
                      <Eye style={{ width: 14, height: 14 }} aria-hidden="true" />
                      Source regions ({selected.region_refs.length})
                    </div>
                    {selected.region_refs.length === 0 ? (
                      <p className="text-xs text-muted">
                        The server returned no region references for this segment.
                      </p>
                    ) : (
                      <ul className="stack gap-2">
                        {selected.region_refs.map((ref) => (
                          <li className="region-ref" key={`${ref.region_id}-${ref.position}`}>
                            <span className="region-ref__page">p.{ref.page_number}</span>
                            <span className="grow" style={{ minWidth: 0 }}>
                              <span className="region-ref__text">
                                {ref.text || "(no text on this line)"}
                              </span>
                              <span className="region-ref__geom">
                                x {ref.bbox_left}, y {ref.bbox_top}, w {ref.bbox_width}, h{" "}
                                {ref.bbox_height} · {ref.review_status}
                              </span>
                            </span>
                          </li>
                        ))}
                      </ul>
                    )}
                    <p className="text-xs text-muted">
                      Coordinates are page-local pixels on the page shown — boxes from different
                      pages are never merged.
                    </p>
                  </div>
                </div>
              </Card>
            </>
          ) : (
            <div className="card card--pad">
              <EmptyState icon={<Split />} title="Select a segment" />
            </div>
          )}
        </div>
      </div>

      {/* --- Confirm dialog (consequential mutation, confirmed) --- */}
      <Modal
        open={confirming}
        onClose={() => setConfirming(false)}
        title="Confirm this structure?"
        description="This records that a human verified the segment's structure and text. It does not judge whether the answer is correct."
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirming(false)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="primary" onClick={() => submitReview("confirm")} loading={busy} icon={<Check />}>
              Record confirmation
            </Button>
          </>
        }
      >
        <div className="seg-detail__text">{selected?.effective_text || selected?.text}</div>
        {reviewError && (
          <div className="mt-3">
            <Alert tone="danger" title="Could not record">{reviewError}</Alert>
          </div>
        )}
      </Modal>

      {/* --- Correction dialog --- */}
      <Modal
        open={correcting}
        onClose={() => setCorrecting(false)}
        title="Correct the segment text"
        description="Your replacement becomes the effective text and is preserved across reprocessing. This corrects the extracted structure — it is not an assessment."
        footer={
          <>
            <Button variant="ghost" onClick={() => setCorrecting(false)} disabled={busy}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => submitReview("correct")}
              loading={busy}
              disabled={!correction.trim()}
              icon={<Save />}
            >
              Submit correction
            </Button>
          </>
        }
      >
        <div className="stack gap-3">
          <Field
            label="Corrected text"
            htmlFor="correction"
            required
            hint="Required by the server when action is 'correct'."
            error={reviewError}
          >
            <textarea
              id="correction"
              className="textarea"
              value={correction}
              onChange={(event) => setCorrection(event.target.value)}
              maxLength={20000}
            />
          </Field>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="text-xs text-muted">
              Original derived text is still available after saving.
            </span>
            <Button
              size="sm"
              variant="ghost"
              icon={<Undo2 />}
              onClick={() => setCorrection(selected?.effective_text || selected?.text || "")}
            >
              Reset
            </Button>
          </div>
        </div>
      </Modal>
    </>
  );
}

function BackLink({ documentId }: { documentId: string }) {
  return (
    <div style={{ marginBottom: "var(--space-4)" }}>
      <Link
        to={`/documents/${documentId}`}
        className="row gap-2 text-sm"
        style={{ color: "var(--text-secondary)" }}
      >
        <ArrowLeft style={{ width: 15, height: 15 }} aria-hidden="true" />
        Back to document
      </Link>
    </div>
  );
}
