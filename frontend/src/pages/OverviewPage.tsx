/**
 * Overview dashboard.
 *
 * Every number shown here comes from a real API response (`Page.total` from
 * `GET /v1/documents`, and per-document statuses). No metric is estimated,
 * interpolated, or hard-coded; when a value is unavailable it is omitted
 * entirely rather than filled with a placeholder.
 */

import { useMemo, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowRight,
  BrainCircuit,
  CalendarClock,
  CheckCircle2,
  FileUp,
  FileStack,
  Layers,
  PenLine,
  RefreshCw,
  ScanText,
  TriangleAlert,
  Upload,
} from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import { useAsyncData } from "@/hooks/useAsyncData";
import { ProcessingPill, SegmentationPill, processingInfo } from "@/components/status";
import { Button, Card, EmptyState, ErrorState, SkeletonRows, Stat } from "@/components/ui";
import * as api from "@/services/api";
import { useSegmentationStatuses } from "@/hooks/useSegmentationStatuses";
import type { SourceDocument } from "@/types/api";

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / Math.pow(1024, index);
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

function formatDateTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const WORKFLOW = [
  {
    icon: <FileUp />,
    title: "Source material",
    desc: "Upload a PDF or image. It is stored privately and never served as a public link.",
  },
  {
    icon: <ScanText />,
    title: "Extracted text",
    desc: "Pages are prepared, then recognized into line-level regions with model provenance.",
  },
  {
    icon: <Layers />,
    title: "Structured segments",
    desc: "Rules group regions into question and answer candidates with page-local evidence.",
  },
  {
    icon: <PenLine />,
    title: "Human review",
    desc: "A reviewer confirms or corrects each segment; the correction is preserved.",
  },
];

export function OverviewPage() {
  const { user } = useAuth();
  const navigate = useNavigate();

  const documents = useAsyncData(
    (signal) => api.listDocuments({ limit: 6, offset: 0 }, signal),
    [],
  );

  const items = useMemo(() => documents.data?.items ?? [], [documents.data]);
  const ids = useMemo(() => items.map((doc) => doc.id), [items]);
  const segmentation = useSegmentationStatuses(ids);

  const inFlight = items.filter((doc) => {
    const info = processingInfo(doc.processing_state);
    return info.active;
  }).length;

  const failed = items.filter((doc) => doc.processing_state === "failed").length;

  // Due reviews are real server data; they are the one action that is not
  // about documents at all, so the page asks for the list directly.
  const dueReviews = useAsyncData((signal) => api.listDueReviews(signal), []);

  /**
   * Next actions are *derived from recorded state only*: each entry names a
   * condition the server actually reported. When nothing needs doing the card
   * says so instead of inventing work.
   */
  const actions = useMemo(() => {
    const total = documents.data?.total ?? 0;
    const readyForOcr = items.filter((d) => d.processing_state === "preprocessed");
    const needsReview = items.filter((d) => d.processing_state === "review_required");
    const failedDocs = items.filter((d) => d.processing_state === "failed");
    const awaitingSegmentation = items.filter(
      (d) =>
        d.processing_state === "completed" &&
        segmentation.statuses[d.id]?.segmentation_status !== "completed" &&
        segmentation.statuses[d.id]?.segmentation_status !== "failed",
    );
    const due = dueReviews.data?.length ?? 0;

    const list: Array<{
      key: string;
      icon: ReactNode;
      title: string;
      detail: string;
      to?: string;
      cta: string;
      tone?: "primary" | "secondary";
    }> = [];

    if (total === 0) {
      list.push({
        key: "upload",
        icon: <Upload />,
        title: "Upload your first document",
        detail:
          "Nothing is stored publicly: the file goes to private storage and only authorized API paths can read it back.",
        to: "/documents?upload=1",
        cta: "Upload",
        tone: "primary",
      });
      return list;
    }

    if (inFlight > 0) {
      list.push({
        key: "inflight",
        icon: <RefreshCw />,
        title: `${inFlight} document${inFlight === 1 ? " is" : "s are"} processing`,
        detail:
          "Preprocessing and recognition run as queued jobs; the stages update themselves on the document page.",
        to: "/documents?filter=active",
        cta: "Watch progress",
      });
    }
    if (failedDocs.length > 0) {
      list.push({
        key: "failed",
        icon: <TriangleAlert />,
        title: `${failedDocs.length} document${failedDocs.length === 1 ? "" : "s"} stopped with an error`,
        detail: "Failed documents keep their partial results and can be retried or deleted.",
        to: "/documents?filter=failed",
        cta: "Review failures",
        tone: "primary",
      });
    }
    if (needsReview.length > 0) {
      list.push({
        key: "review",
        icon: <PenLine />,
        title: `${needsReview.length} document${needsReview.length === 1 ? "" : "s"} waiting for human review`,
        detail: "Recognition finished but flagged output a person should confirm or correct.",
        to: "/documents?filter=review",
        cta: "Open the queue",
        tone: "primary",
      });
    }
    if (readyForOcr.length > 0) {
      list.push({
        key: "ocr",
        icon: <ScanText />,
        title: `${readyForOcr.length} document${readyForOcr.length === 1 ? "" : "s"} prepared but not yet recognized`,
        detail:
          "Page images exist; text recognition has not run. Running it produces observed content, not verified truth.",
        to: "/documents?filter=processed",
        cta: "Prepare recognition",
      });
    }
    if (awaitingSegmentation.length > 0) {
      list.push({
        key: "segment",
        icon: <Layers />,
        title: `${awaitingSegmentation.length} recognized document${awaitingSegmentation.length === 1 ? "" : "s"} without segments`,
        detail:
          "Segmentation groups recognized lines into question and answer candidates — inferred structure a reviewer confirms.",
        to: "/documents?filter=processed",
        cta: "Review candidates",
      });
    }
    if (due > 0) {
      list.push({
        key: "reviews",
        icon: <CalendarClock />,
        title: `${due} retention review${due === 1 ? "" : "s"} due`,
        detail: "Scheduled by the spaced-repetition service from your recorded study history.",
        to: "/retention",
        cta: "Open retention",
      });
    }

    return list;
  }, [
    documents.data?.total,
    items,
    inFlight,
    segmentation.statuses,
    dueReviews.data,
  ]);

  return (
    <>
      <div className="page-head">
        <div className="page-head__title">
          <span className="page-head__eyebrow">Workspace</span>
          <h1>
            {user?.display_name ? `Welcome back, ${user.display_name.split(" ")[0]}` : "Welcome to Unmist"}
          </h1>
          <p className="page-head__desc">
            Unmist links what you learn to the evidence behind it: upload source material,
            read the recognized text, review the extracted question and answer structure, and
            keep every claim traceable to a page.
          </p>
        </div>
        <div className="page-head__actions">
          <Link to="/documents?upload=1">
            <Button variant="primary" size="lg" icon={<Upload />}>
              Upload document
            </Button>
          </Link>
          <Link to="/knowledge">
            <Button variant="secondary" size="lg" icon={<BrainCircuit />}>
              Explore knowledge
            </Button>
          </Link>
        </div>
      </div>

      {documents.error ? (
        <ErrorState error={documents.error} onRetry={documents.reload} />
      ) : (
        <>
          <div className="grid grid--4" style={{ marginBottom: "var(--space-6)" }}>
            <Stat
              label="Documents"
              icon={<FileStack />}
              value={documents.initialLoading ? "—" : documents.data?.total ?? 0}
              note="From GET /v1/documents"
            />
            <Stat
              label="Active now"
              icon={<RefreshCw />}
              value={documents.initialLoading ? "—" : inFlight}
              note="Among the 6 most recent"
            />
            <Stat
              label="Needs attention"
              icon={<PenLine />}
              value={documents.initialLoading ? "—" : failed}
              note="Failed, among the 6 most recent"
            />
            <Stat
              label="Segmented"
              icon={<Layers />}
              value={
                documents.initialLoading
                  ? "—"
                  : items.filter((d) => segmentation.statuses[d.id]?.segmentation_status === "completed")
                      .length
              }
              note="Counted on this page only"
            />
          </div>

          <div className="grid grid--2" style={{ gridTemplateColumns: "1.6fr 1fr" }}>
            <Card
              title="Recent documents"
              icon={<FileStack />}
              action={
                items.length > 0 ? (
                  <Link to="/documents">
                    <Button variant="ghost" size="sm" icon={<ArrowRight />}>
                      View all
                    </Button>
                  </Link>
                ) : undefined
              }
              pad={false}
            >
              {documents.initialLoading ? (
                <SkeletonRows rows={4} />
              ) : items.length === 0 ? (
                <EmptyState
                  icon={<Upload />}
                  title="No documents yet"
                  description="Upload your first PDF or image to see preprocessing, recognition, and segmentation happen step by step."
                  action={
                    <Link to="/documents?upload=1">
                      <Button variant="primary" icon={<Upload />}>
                        Upload document
                      </Button>
                    </Link>
                  }
                />
              ) : (
                <ul>
                  {items.map((doc) => (
                    <DocumentRow
                      key={doc.id}
                      document={doc}
                      segmentStatus={segmentation.statuses[doc.id]?.segmentation_status ?? null}
                      onOpen={() => navigate(`/documents/${doc.id}`)}
                    />
                  ))}
                </ul>
              )}
            </Card>

            <div className="stack gap-5">
              <Card title="How Unmist works" icon={<BrainCircuit />}>
                <div className="stack gap-3">
                  {WORKFLOW.map((step, index) => (
                    <div className="row gap-3" key={step.title} style={{ alignItems: "flex-start" }}>
                      <span className="flow__num" style={{ marginBottom: 0, flex: "none" }}>
                        {index + 1}
                      </span>
                      <div style={{ minWidth: 0 }}>
                        <div className="flow__title">{step.title}</div>
                        <div className="flow__desc">{step.desc}</div>
                      </div>
                    </div>
                  ))}
                </div>
              </Card>

              <Card
                title="Next actions"
                icon={<CheckCircle2 />}
                footer={
                  <span className="text-xs text-muted">
                    {items.length === 0
                      ? "Derived from your document list and due-review list — nothing has been recorded yet, so nothing is suggested."
                      : `Derived from the ${items.length} most recent document${items.length === 1 ? "" : "s"} plus your due-review list — no prediction, no recommendation beyond recorded state.`}
                  </span>
                }
              >
                {documents.initialLoading ? (
                  <SkeletonRows rows={3} />
                ) : actions.length === 0 ? (
                  <div className="stack gap-3 text-sm">
                    <p>
                      <strong>Nothing is waiting on you.</strong> Every loaded document is in a
                      settled state and no reviews are due.
                    </p>
                    <p className="text-secondary">
                      New work appears here the moment the server records it — a failed run, a
                      document ready for recognition, or a scheduled review.
                    </p>
                    <Link to="/learntrace">
                      <Button variant="secondary" size="sm" icon={<BrainCircuit />}>
                        Run a prerequisite diagnosis
                      </Button>
                    </Link>
                  </div>
                ) : (
                  <ol className="stack gap-4">
                    {actions.map((action, index) => (
                      <li key={action.key} className="row gap-3" style={{ alignItems: "flex-start" }}>
                        <span className="flow__num" style={{ marginBottom: 0, flex: "none" }}>
                          {index + 1}
                        </span>
                        <div style={{ minWidth: 0, flexGrow: 1 }}>
                          <div className="flow__title">{action.title}</div>
                          <div className="flow__desc">{action.detail}</div>
                        </div>
                        <Link to={action.to ?? "/documents"} style={{ flex: "none" }}>
                          <Button
                            size="sm"
                            variant={action.tone === "primary" ? "primary" : "secondary"}
                          >
                            {action.cta}
                          </Button>
                        </Link>
                      </li>
                    ))}
                  </ol>
                )}
              </Card>
            </div>
          </div>
        </>
      )}
    </>
  );
}

function DocumentRow({
  document,
  segmentStatus,
  onOpen,
}: {
  document: SourceDocument;
  segmentStatus: string | null;
  onOpen: () => void;
}) {
  return (
    <li className="doc-row" onClick={onOpen}>
      <span className="doc-row__icon" aria-hidden="true">
        <FileStack />
      </span>
      <div className="doc-row__body">
        <span className="doc-row__title" title={document.original_filename ?? document.id}>
          {document.original_filename || "Untitled document"}
        </span>
        <div className="doc-row__meta">
          <span>{formatDateTime(document.created_at)}</span>
          <span aria-hidden="true">·</span>
          <span>{formatBytes(document.size_bytes)}</span>
          {document.page_count > 0 && (
            <>
              <span aria-hidden="true">·</span>
              <span>
                {document.page_count} page{document.page_count === 1 ? "" : "s"}
              </span>
            </>
          )}
        </div>
      </div>
      <div className="doc-row__badges">
        <ProcessingPill state={document.processing_state} />
        <SegmentationPill status={segmentStatus} />
      </div>
      <div className="doc-row__actions">
        <Button
          size="sm"
          variant="secondary"
          onClick={(event) => {
            event.stopPropagation();
            onOpen();
          }}
        >
          Open
        </Button>
      </div>
    </li>
  );
}
