/**
 * Authentication session.
 *
 * Contract: the API issues `access_token` + `refresh_token` from
 * `POST /v1/auth/login` and `POST /v1/auth/register`, accepts
 * `Authorization: Bearer <access_token>`, and implements a real refresh flow at
 * `POST /v1/auth/refresh`.
 *
 * Storage strategy:
 *   - The ACCESS token lives in memory only. It is never written to
 *     localStorage or sessionStorage, so it cannot be read back after the tab
 *     closes and is not persisted across reloads.
 *   - The REFRESH token is kept in sessionStorage (per-tab, cleared when the
 *     tab closes) purely so a page reload during a demo does not force a
 *     re-login. The access token is re-minted from it on boot. If you prefer a
 *     fully memory-only session, set `VITE_PERSIST_REFRESH=0` and the client
 *     will simply return to the sign-in screen after every reload.
 *   - Nothing is ever placed in localStorage.
 */

const REFRESH_STORAGE_KEY = "unmist.refresh_token";
const PERSIST_REFRESH =
  typeof import.meta !== "undefined" &&
  (import.meta.env?.VITE_PERSIST_REFRESH as string | undefined) !== "0";

let accessToken: string | null = null;
let refreshToken: string | null = null;
let expiresAt = 0;
let refreshInFlight: Promise<string | null> | null = null;

type AuthListener = (signedIn: boolean) => void;
const listeners = new Set<AuthListener>();

function emit(): void {
  const signedIn = accessToken !== null;
  listeners.forEach((fn) => fn(signedIn));
}

export function subscribeAuth(fn: AuthListener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function readPersistedRefresh(): string | null {
  if (!PERSIST_REFRESH) return null;
  try {
    return window.sessionStorage.getItem(REFRESH_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writePersistedRefresh(token: string | null): void {
  if (!PERSIST_REFRESH) return;
  try {
    if (token) window.sessionStorage.setItem(REFRESH_STORAGE_KEY, token);
    else window.sessionStorage.removeItem(REFRESH_STORAGE_KEY);
  } catch {
    /* storage disabled — session simply does not survive reloads */
  }
}

/** Restore a session from a persisted refresh token. Call once on boot. */
export function bootstrapSession(): Promise<boolean> {
  if (accessToken) return Promise.resolve(true);
  const stored = readPersistedRefresh();
  if (!stored) return Promise.resolve(false);
  refreshToken = stored;
  return refreshAccessToken().then((token) => token !== null);
}

export function setSession(input: {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}): void {
  accessToken = input.access_token;
  refreshToken = input.refresh_token;
  expiresAt = Date.now() + Math.max(0, input.expires_in - 30) * 1000;
  writePersistedRefresh(input.refresh_token);
  emit();
}

export function clearSession(): void {
  accessToken = null;
  refreshToken = null;
  expiresAt = 0;
  writePersistedRefresh(null);
  emit();
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function isSignedIn(): boolean {
  return accessToken !== null;
}

/**
 * True when the access token is missing or within 60s of expiring, so callers
 * can refresh proactively instead of gambling on a 401 round trip.
 */
export function accessTokenStale(): boolean {
  if (!accessToken) return true;
  return Date.now() >= expiresAt;
}

/**
 * Exchange the refresh token for a fresh access token.
 *
 * Concurrent callers share one in-flight request (deduplicated), and failure
 * clears the session rather than looping. Returns null when no refresh is
 * possible.
 */
export function refreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;

  const token = refreshToken ?? readPersistedRefresh();
  if (!token) {
    clearSession();
    return Promise.resolve(null);
  }

  const base = resolveBase();
  refreshInFlight = fetch(`${base}/v1/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: token }),
  })
    .then(async (res) => {
      if (!res.ok) {
        clearSession();
        return null;
      }
      const data = (await res.json()) as {
        access_token: string;
        refresh_token: string;
        expires_in: number;
      };
      // Do not call setSession() here: it would re-enter refresh logic.
      accessToken = data.access_token;
      refreshToken = data.refresh_token;
      expiresAt = Date.now() + Math.max(0, data.expires_in - 30) * 1000;
      writePersistedRefresh(data.refresh_token);
      emit();
      return accessToken;
    })
    .catch(() => {
      clearSession();
      return null;
    })
    .finally(() => {
      refreshInFlight = null;
    });

  return refreshInFlight;
}

/** Resolve the API base once, shared with the http client. */
export function resolveBase(): string {
  const raw = (import.meta.env?.VITE_API_BASE_URL as string | undefined) || "";
  const trimmed = raw.replace(/\/+$/, "");
  // `""` means "same origin". The backend mounts every router under `/api/v1`,
  // so the same-origin default must be `/api` for the Vite dev/preview proxy
  // (`/api` -> http://localhost:8000) to forward calls to the right place.
  // Endpoint paths in api.ts are written relative to `/v1`, so the base is the
  // full prefix *up to* `/v1` — e.g. `https://host/api`, not `https://host/v1`.
  return trimmed || "/api";
}
