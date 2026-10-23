/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Absolute API base ("" = same origin, use the dev proxy). */
  readonly VITE_API_BASE_URL?: string;
  /** Dev/preview proxy target when VITE_API_BASE_URL is unset. */
  readonly VITE_API_PROXY_TARGET?: string;
  /** Request timeout in ms. */
  readonly VITE_API_TIMEOUT_MS?: string;
  /** "0" disables persisting the refresh token across reloads. */
  readonly VITE_PERSIST_REFRESH?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
