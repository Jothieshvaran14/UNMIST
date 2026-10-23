/**
 * Minimal typed HTTP client for the Unmist API.
 *
 * Responsibilities kept in one place, as required:
 *   - base URL construction
 *   - JSON + multipart requests
 *   - response parsing
 *   - error normalisation into `ApiClientError`
 *   - bearer authentication and a single transparent refresh-and-retry
 *   - request cancellation (via `AbortSignal`)
 *
 * It deliberately does NOT assume every endpoint shares one envelope: JSON
 * bodies are returned as-is and typed by the caller, while `Page<T>` and plain
 * arrays are handled by the specific endpoint functions.
 */

import {
  accessTokenStale,
  clearSession,
  getAccessToken,
  refreshAccessToken,
  resolveBase,
} from "./session";
import type { ApiErrorBody } from "@/types/api";

export type ApiErrorCode =
  | "network_error"
  | "timeout"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "validation"
  | "server_error"
  | "unexpected_response"
  | "aborted"
  | "unknown";

/** A normalised, user-safe error. Never contains stack traces or paths. */
export class ApiClientError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode | string;
  readonly details: unknown;

  constructor(
    message: string,
    opts: { status: number; code: string; details?: unknown },
  ) {
    super(message);
    this.name = "ApiClientError";
    this.status = opts.status;
    this.code = opts.code;
    this.details = opts.details;
  }

  get isAborted(): boolean {
    return this.code === "aborted";
  }

  /** True when retrying the exact same request could plausibly succeed. */
  get isRetryable(): boolean {
    return (
      this.status === 0 ||
      this.status === 408 ||
      this.status === 429 ||
      this.status >= 500
    );
  }
}

const TIMEOUT_MS = Number(import.meta.env?.VITE_API_TIMEOUT_MS) || 20000;

/** Map an HTTP status onto a friendly, non-leaky sentence. */
function friendlyMessage(status: number, fallback: string): string {
  switch (status) {
    case 400:
    case 422:
      return "Some information was not accepted. Check the highlighted fields and try again.";
    case 401:
      return "Your session has expired. Please sign in again.";
    case 403:
      return "You do not have permission to do that.";
    case 404:
      return "That item could not be found.";
    case 409:
      return fallback || "That action conflicts with the current state.";
    case 413:
      return "That file is larger than the server allows.";
    case 0:
      return "Cannot reach the Unmist API. Check that the backend is running.";
    default:
      if (status >= 500) {
        return "The server could not complete that request. Please try again.";
      }
      return fallback || "The request could not be completed.";
  }
}

function statusToCode(status: number): ApiErrorCode {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 400 || status === 422) return "validation";
  if (status >= 500) return "server_error";
  return "unknown";
}

/** True when a message is safe to show verbatim (backend already bounds these). */
function isSafeBackendMessage(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= 300;
}

function buildUrl(path: string, query?: Record<string, string | number | undefined>): string {
  const base = resolveBase();
  const normalised = path.startsWith("/") ? path : `/${path}`;
  let url = `${base}${normalised}`;
  if (query) {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "") {
        params.set(key, String(value));
      }
    });
    const qs = params.toString();
    if (qs) url += `?${qs}`;
  }
  return url;
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  signal?: AbortSignal;
  /** Skip the Authorization header (login/register/health). */
  anonymous?: boolean;
  /** Internal: prevents an infinite refresh-retry loop. */
  _retried?: boolean;
  headers?: Record<string, string>;
}

async function parseError(response: Response): Promise<ApiClientError> {
  let payload: unknown = null;
  try {
    const text = await response.text();
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null;
  }

  const envelope = payload as Partial<ApiErrorBody> | null;
  const serverMessage = envelope?.error?.message;
  const serverCode = envelope?.error?.code;

  const status = response.status;
  const code = serverCode || statusToCode(status);
  const message = isSafeBackendMessage(serverMessage)
    ? status >= 500
      ? friendlyMessage(status, serverMessage)
      : serverMessage
    : friendlyMessage(status, "");

  return new ApiClientError(message, {
    status,
    code,
    details: envelope?.error?.details,
  });
}

/** Core JSON request. One refresh-and-retry on 401, never more. */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, query, signal, anonymous } = options;

  if (!anonymous && accessTokenStale() && !options._retried) {
    await refreshAccessToken();
  }

  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  if (body !== undefined && !(body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  if (!anonymous) {
    const token = getAccessToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), TIMEOUT_MS);
  const onAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", onAbort, { once: true });
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      headers,
      body:
        body === undefined
          ? undefined
          : body instanceof FormData
            ? body
            : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (err) {
    clearTimeout(timeoutId);
    if (signal?.aborted) {
      throw new ApiClientError("Request cancelled", { status: 0, code: "aborted" });
    }
    // Distinguish a genuine timeout from a connection failure.
    const isTimeout = err instanceof DOMException && err.name === "AbortError" && !signal?.aborted;
    if (isTimeout) {
      throw new ApiClientError("The request timed out. Please try again.", {
        status: 0,
        code: "timeout",
      });
    }
    throw new ApiClientError(
      "Cannot reach the Unmist API. Check that the backend is running.",
      { status: 0, code: "network_error" },
    );
  } finally {
    clearTimeout(timeoutId);
    if (signal) signal.removeEventListener("abort", onAbort);
  }

  if (response.status === 401 && !anonymous && !options._retried) {
    const refreshed = await refreshAccessToken();
    if (refreshed) {
      return request<T>(path, { ...options, _retried: true });
    }
    clearSession();
    const err = await parseError(response);
    throw err.status === 401
      ? err
      : new ApiClientError("Your session has expired. Please sign in again.", {
          status: 401,
          code: "unauthorized",
        });
  }

  if (!response.ok) {
    if (response.status === 401) clearSession();
    throw await parseError(response);
  }

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiClientError("Received an unexpected response from the server.", {
      status: response.status,
      code: "unexpected_response",
    });
  }
}

export interface UploadProgress {
  loaded: number;
  total: number;
  percent: number;
}

/**
 * Multipart upload with real progress events.
 *
 * `fetch` cannot report upload progress without streaming request bodies, so
 * this one call uses XMLHttpRequest. Progress here is bytes actually sent to
 * the server — never an invented percentage.
 */
export function uploadWithProgress<T>(
  path: string,
  form: FormData,
  opts: {
    signal?: AbortSignal;
    onProgress?: (p: UploadProgress) => void;
  } = {},
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", buildUrl(path), true);
    xhr.responseType = "text";
    xhr.setRequestHeader("Accept", "application/json");

    const token = getAccessToken();
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);

    xhr.upload.onprogress = (event) => {
      if (!opts.onProgress || event.lengthComputable === false) return;
      opts.onProgress({
        loaded: event.loaded,
        total: event.total,
        percent: Math.min(100, Math.round((event.loaded / event.total) * 100)),
      });
    };

    xhr.onload = () => {
      const raw = xhr.responseText;
      let payload: unknown = null;
      try {
        payload = raw ? JSON.parse(raw) : null;
      } catch {
        payload = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload as T);
        return;
      }
      const envelope = payload as Partial<ApiErrorBody> | null;
      const serverMessage = envelope?.error?.message;
      const status = xhr.status;
      const message =
        isSafeBackendMessage(serverMessage) && status < 500
          ? serverMessage
          : friendlyMessage(status, "");
      reject(
        new ApiClientError(message, {
          status,
          code: envelope?.error?.code || statusToCode(status),
          details: envelope?.error?.details,
        }),
      );
    };

    xhr.onerror = () => {
      reject(
        new ApiClientError("Cannot reach the Unmist API. Check that the backend is running.", {
          status: 0,
          code: "network_error",
        }),
      );
    };

    xhr.ontimeout = () => {
      reject(new ApiClientError("The upload timed out. Please try again.", {
        status: 0,
        code: "timeout",
      }));
    };

    xhr.onabort = () => {
      reject(new ApiClientError("Upload cancelled", { status: 0, code: "aborted" }));
    };

    if (opts.signal) {
      if (opts.signal.aborted) {
        xhr.abort();
        return;
      }
      opts.signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }

    xhr.send(form);
  });
}

/** Abandon an in-flight request; used by effects on unmount. */
export function abortError(): ApiClientError {
  return new ApiClientError("Request cancelled", { status: 0, code: "aborted" });
}
