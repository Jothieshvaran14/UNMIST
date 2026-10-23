/**
 * Document library.
 *
 * Shows real records from `GET /v1/documents`. OCR lifecycle and segmentation
 * lifecycle are rendered as two independent badges — segmentation status is
 * fetched per document because the list payload does not carry it.
 *
 * Search and filtering operate on the records actually loaded from the server
 * (the API has no server-side search parameter); that limitation is stated in
 * the UI rather than hidden.
 */

import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { FileStack, Filter, Plus, RefreshCw, Search, Upload, UploadCloud } from "lucide-react";
import { useAsyncData } from "@/hooks/useAsyncData";
import { useSegmentationStatuses } from "@/hooks/useSegmentationStatuses";
import { ProcessingPill, SegmentationPill, processingInfo } from "@/components/status";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  SkeletonRows,
} from "@/components/ui";
import { UploadModal } from "@/components/UploadModal";
import * as api from "@/services/api";
import type { SourceDocument } from "@/types/api";

const PAGE_SIZE = 100;

type FilterKey = "all" | "active" | "processed" | "review" | "failed" | "deleting";

const FILTERS: Array<{ key: FilterKey; label: string; matches: (state: string | null) => boolean }> =
  [
    { key: "all", label: "All", matches: () => true },
    { key: "active", label: "In progress", matches: (s) => processingInfo(s).active && s !== "deletion_pending" },
    { key: "processed", label: "Processed", matches: (s) => s === "completed" || s === "preprocessed" },
    { key: "review", label: "Needs review", matches: (s) => s === "review_required" },
    { key: "failed", label: "Failed", matches: (s) => s === "failed" },
    {
      key: "deleting",
      label: "Deletion pending",
      matches: (s) => s === "deletion_pending" || s === "deleted",
    },
  ];

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / Math.pow(1024, index);
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function DocumentsPage() {
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<FilterKey>("all");
  const [extra, setExtra] = useState<SourceDocument[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);

  // `?upload=1` opens the uploader directly (used by the Overview shortcut).
  useEffect(() => {
    if (params.get("upload") === "1") {
      setUploadOpen(true);
      const next = new URLSearchParams(params);
      next.delete("upload");
      setParams(next, { replace: true });
    }
  }, [params, setParams]);

  const base = useAsyncData(
    (signal) => api.listDocuments({ limit: PAGE_SIZE, offset: 0 }, signal),
    [],
  );

  const records = useMemo(() => [...(base.data?.items ?? []), ...extra], [base.data, extra]);

  const ids = useMemo(() => records.map((doc) => doc.id), [records]);
  const segmentation = useSegmentationStatuses(ids);

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const rule = FILTERS.find((entry) => entry.key === filter) ?? FILTERS[0];
    return records.filter((doc) => {
      if (!rule.matches(doc.processing_state)) return false;
      if (!needle) return true;
      return (doc.original_filename ?? "").toLowerCase().includes(needle) ||
        doc.id.toLowerCase().includes(needle);
    });
  }, [records, search, filter]);

  const total = base.data?.total ?? 0;
  const haveAll = records.length >= total || (base.data?.items.length ?? 0) < PAGE_SIZE;

  async function loadMore() {
    setLoadingMore(true);
    try {
      const next = await api.listDocuments({ limit: PAGE_SIZE, offset: records.length });
      setExtra((prev) => [...prev, ...next.items]);
    } catch {
      base.reload();
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div className="page-head__title">
          <span className="page-head__eyebrow">Library</span>
          <h1>Documents</h1>
          <p className="page-head__desc">
            Every uploaded source, with its recognition state and segmentation state shown
            separately. Select a document to inspect its stages, regions, and segments.
          </p>
        </div>
        <div className="page-head__actions">
          <Button variant="ghost" onClick={base.reload} icon={<RefreshCw />} disabled={base.refreshing}>
            Refresh
          </Button>
          <Button variant="primary" onClick={() => setUploadOpen(true)} icon={<Upload />}>
            Upload document
          </Button>
        </div>
      </div>

      <Card
        title={
          <span className="row gap-3 wrap" style={{ width: "100%" }}>
            <span className="row gap-2 grow" style={{ minWidth: 200, maxWidth: 340 }}>
              <Search aria-hidden="true" style={{ width: 15, height: 15, color: "var(--text-muted)" }} />
              <input
                className="input"
                type="search"
                placeholder="Search by file name…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                aria-label="Search documents by file name"
              />
            </span>
            <span className="row gap-2 wrap">
              <Filter aria-hidden="true" style={{ width: 14, height: 14, color: "var(--text-muted)" }} />
              <span className="segmented" role="group" aria-label="Filter by status">
                {FILTERS.map((entry) => (
                  <button
                    key={entry.key}
                    type="button"
                    className="segmented__btn"
                    aria-pressed={filter === entry.key}
                    onClick={() => setFilter(entry.key)}
                  >
                    {entry.label}
                  </button>
                ))}
              </span>
            </span>
          </span>
        }
        pad={false}
      >
        {base.initialLoading ? (
          <SkeletonRows rows={5} />
        ) : base.error ? (
          <ErrorState error={base.error} onRetry={base.reload} />
        ) : records.length === 0 ? (
          <EmptyState
            icon={<UploadCloud />}
            title="No documents yet"
            description="Upload a PDF or image to begin. You will see preprocessing, recognition, and segmentation as separate, honest stages."
            action={
              <Button variant="primary" onClick={() => setUploadOpen(true)} icon={<Plus />}>
                Upload your first document
              </Button>
            }
          />
        ) : (
          <>
            <div className="table-wrap">
              <table className="table">
                <caption className="sr-only">
                  Uploaded documents with recognition and segmentation status
                </caption>
                <thead>
                  <tr>
                    <th scope="col">Document</th>
                    <th scope="col">Uploaded</th>
                    <th scope="col">Recognition</th>
                    <th scope="col">Segmentation</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((doc) => (
                    <tr key={doc.id}>
                      <td>
                        <div className="row gap-3">
                          <span className="doc-row__icon" aria-hidden="true" style={{ width: 32, height: 32 }}>
                            <FileStack />
                          </span>
                          <div style={{ minWidth: 0 }}>
                            <Link
                              to={`/documents/${doc.id}`}
                              style={{ color: "var(--text)", fontWeight: 500 }}
                              className="truncate"
                            >
                              {doc.original_filename || "Untitled document"}
                            </Link>
                            <div className="text-xs text-muted">
                              {doc.media_type} · {formatBytes(doc.size_bytes)}
                              {doc.page_count > 0 ? ` · ${doc.page_count} pages` : ""}
                            </div>
                          </div>
                        </div>
                      </td>
                      <td className="text-sm text-secondary">{formatDate(doc.created_at)}</td>
                      <td>
                        <ProcessingPill state={doc.processing_state} />
                      </td>
                      <td>
                        <SegmentationPill
                          status={segmentation.statuses[doc.id]?.segmentation_status ?? null}
                        />
                      </td>
                      <td>
                        <div className="row gap-2" style={{ justifyContent: "flex-end" }}>
                          <Link to={`/documents/${doc.id}`}>
                            <Button size="sm" variant="secondary">
                              Open
                            </Button>
                          </Link>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {visible.length === 0 && (
              <EmptyState
                icon={<Search />}
                title="No matching documents"
                description="No loaded document matches the current search or filter. Clear them to see the full library."
                action={
                  <Button
                    variant="secondary"
                    onClick={() => {
                      setSearch("");
                      setFilter("all");
                    }}
                  >
                    Clear search and filters
                  </Button>
                }
              />
            )}

            <footer
              className="card__foot row gap-3 wrap"
              style={{ borderRadius: 0 }}
            >
              <span className="text-xs text-muted">
                Showing {visible.length} of {total} document{total === 1 ? "" : "s"}
                {records.length < total && ` · ${records.length} loaded`}
                {segmentation.error ? " · some segmentation states are unavailable" : ""}
              </span>
              {!haveAll && (
                <span style={{ marginLeft: "auto" }}>
                  <Button size="sm" variant="secondary" onClick={loadMore} loading={loadingMore}>
                    Load more
                  </Button>
                </span>
              )}
            </footer>
          </>
        )}
      </Card>

      <UploadModal
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploaded={() => {
          base.reload();
          segmentation.reload();
        }}
      />
    </>
  );
}
