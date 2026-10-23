/**
 * Drag-and-drop upload dialog.
 *
 * Behaviour:
 *   - validates extension and size in the browser *for usability only*; the
 *     backend validation (`UPLOAD_ALLOWED_FORMATS`, `UPLOAD_MAX_BYTES`) is
 *     authoritative and its errors are surfaced verbatim
 *   - reports real byte progress measured by the HTTP layer
 *   - never reports success before the server responds with the created
 *     document; on success it shows the server's id and state
 *   - offers a retry only when the failure is safe to retry
 */

import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { useNavigate } from "react-router-dom";
import { FileText, RotateCw, UploadCloud, X } from "lucide-react";
import { Alert, Button, Modal, ProgressBar } from "@/components/ui";
import { toUserMessage } from "@/components/ui";
import { ApiClientError } from "@/services/http";
import * as api from "@/services/api";
import type { SourceDocument } from "@/types/api";

/** Mirrors `UPLOAD_ALLOWED_FORMATS` / `UPLOAD_MAX_BYTES` defaults. */
const ALLOWED_EXTENSIONS = ["pdf", "jpg", "jpeg", "png", "tiff"];
const MAX_BYTES = 20 * 1024 * 1024;

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / Math.pow(1024, index);
  return `${value >= 10 || index === 0 ? Math.round(value) : value.toFixed(1)} ${units[index]}`;
}

interface Props {
  open: boolean;
  onClose: () => void;
  onUploaded: (document: SourceDocument) => void;
}

type Phase = "select" | "uploading" | "done" | "error";

export function UploadModal({ open, onClose, onUploaded }: Props) {
  const navigate = useNavigate();
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("select");
  const [percent, setPercent] = useState(0);
  const [message, setMessage] = useState<string | null>(null);
  const [created, setCreated] = useState<SourceDocument | null>(null);
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Reset whenever the dialog is reopened.
  useEffect(() => {
    if (open) {
      setFile(null);
      setPhase("select");
      setPercent(0);
      setMessage(null);
      setCreated(null);
      setLocalError(null);
      setDragging(false);
    }
  }, [open]);

  // Abort an in-flight upload if the dialog closes or the page unmounts.
  useEffect(() => {
    if (!open) abortRef.current?.abort();
    return () => abortRef.current?.abort();
  }, [open]);

  const pickFile = useCallback(
    (candidate: File | null | undefined) => {
      if (!candidate) return;
      setLocalError(null);
      setMessage(null);

      const ext = candidate.name.split(".").pop()?.toLowerCase() ?? "";
      if (!ALLOWED_EXTENSIONS.includes(ext)) {
        setFile(null);
        setLocalError(
          `“${candidate.name}” is not a supported format. Allowed: ${ALLOWED_EXTENSIONS.join(", ")}.`,
        );
        return;
      }
      if (candidate.size > MAX_BYTES) {
        setFile(null);
        setLocalError(
          `“${candidate.name}” is ${formatBytes(candidate.size)}. The upload limit is ${formatBytes(MAX_BYTES)}.`,
        );
        return;
      }
      setFile(candidate);
      setPhase("select");
    },
    [],
  );

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (phase === "uploading") return;
    pickFile(event.dataTransfer.files?.[0]);
  }

  async function startUpload() {
    if (!file) return;
    setPhase("uploading");
    setPercent(0);
    setMessage(null);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const doc = await api.uploadDocument(file, {
        signal: controller.signal,
        onProgress: (progress) => setPercent(progress.percent),
      });
      setPercent(100);
      setCreated(doc);
      setPhase("done");
      onUploaded(doc);
    } catch (err) {
      if (err instanceof ApiClientError && err.isAborted) {
        setPhase("select");
        return;
      }
      setMessage(toUserMessage(err));
      setPhase("error");
    }
  }

  const busy = phase === "uploading";

  return (
    <Modal
      open={open}
      onClose={busy ? () => undefined : onClose}
      title="Upload a document"
      description="PDF, JPG, JPEG, PNG or TIFF · up to 20 MB. The server validates every file."
      footer={
        phase === "done" ? (
          <>
            <Button variant="secondary" onClick={onClose}>
              Close
            </Button>
            {created && (
              <Button
                variant="primary"
                onClick={() => {
                  const target = `/documents/${created.id}`;
                  onClose();
                  navigate(target);
                }}
              >
                Open document
              </Button>
            )}
          </>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={startUpload}
              disabled={!file || busy}
              loading={busy}
              icon={phase === "error" ? <RotateCw /> : <UploadCloud />}
            >
              {phase === "error" ? "Retry upload" : busy ? "Uploading…" : "Upload"}
            </Button>
          </>
        )
      }
    >
      {phase === "done" && created ? (
        <div className="stack gap-4">
          <Alert tone="success" title="Upload confirmed by the server">
            The document was created with state <strong>{created.processing_state}</strong>.
          </Alert>
          <div className="file-chip">
            <span className="file-chip__icon" aria-hidden="true">
              <FileText />
            </span>
            <div className="file-chip__meta">
              <div className="file-chip__name">{created.original_filename || "Untitled"}</div>
              <div className="file-chip__size">
                {formatBytes(created.size_bytes)} · id {created.id.slice(0, 8)}…
              </div>
            </div>
          </div>
          <p className="text-xs text-muted">
            Upload succeeded — preprocessing, recognition and segmentation have <em>not</em>{" "}
            run yet. Open the document to watch each stage.
          </p>
        </div>
      ) : (
        <div className="stack gap-4">
          {phase === "error" && message && (
            <Alert
              tone="danger"
              title="Upload failed"
              action={
                <Button size="sm" variant="secondary" onClick={startUpload} icon={<RotateCw />}>
                  Retry
                </Button>
              }
            >
              {message}
            </Alert>
          )}

          {localError && <Alert tone="warning" title="File not accepted">{localError}</Alert>}

          {!file ? (
            <div
              className={`dropzone ${dragging ? "is-over" : ""} ${busy ? "is-disabled" : ""}`.trim()}
              role="button"
              tabIndex={0}
              onClick={() => inputRef.current?.click()}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  inputRef.current?.click();
                }
              }}
              onDragOver={(event) => {
                event.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              aria-label="Choose a file or drop one here"
            >
              <span className="dropzone__icon" aria-hidden="true">
                <UploadCloud />
              </span>
              <span className="dropzone__title">Drop a file here</span>
              <span className="dropzone__hint">
                or click to browse · {ALLOWED_EXTENSIONS.join(", ")} · max {formatBytes(MAX_BYTES)}
              </span>
            </div>
          ) : (
            <div className="file-chip">
              <span className="file-chip__icon" aria-hidden="true">
                <FileText />
              </span>
              <div className="file-chip__meta">
                <div className="file-chip__name" title={file.name}>
                  {file.name}
                </div>
                <div className="file-chip__size">{formatBytes(file.size)}</div>
              </div>
              <button
                className="btn btn--ghost btn--sm btn--icon"
                onClick={() => {
                  setFile(null);
                  setPhase("select");
                  setPercent(0);
                }}
                aria-label="Remove selected file"
                disabled={busy}
              >
                <X />
              </button>
            </div>
          )}

          {busy && (
            <div className="stack gap-2">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="text-xs text-muted">Sending to server…</span>
                <span className="text-xs text-muted mono">{percent}%</span>
              </div>
              <ProgressBar value={percent} label="Upload progress" />
            </div>
          )}

          <input
            ref={inputRef}
            type="file"
            className="sr-only"
            accept={ALLOWED_EXTENSIONS.map((ext) => `.${ext}`).join(",")}
            onChange={(event) => {
              pickFile(event.target.files?.[0]);
              event.target.value = "";
            }}
            tabIndex={-1}
            aria-hidden="true"
          />
        </div>
      )}
    </Modal>
  );
}
