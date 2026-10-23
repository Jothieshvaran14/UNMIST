"""Centralized, validated configuration.

Configuration is read from environment variables and validated at import time.
Invalid configuration fails loudly and safely: the application refuses to start
rather than running with unsafe defaults.

Security notes:
  * ``SECRET_KEY`` must be a long random value. In production a placeholder or a
    short value is rejected.
  * Outbound retrieval is disabled by default (empty allowlist).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class ConfigurationError(RuntimeError):
    """Raised when the environment configuration is invalid."""


_PLACEHOLDER_SECRETS = {
    "change-me-generate-a-long-random-secret",
    "changeme",
    "secret",
    "dev-secret",
    "",
}

_VALID_ENVIRONMENTS = {"development", "test", "staging", "production"}
# PDF rasterization backends. "pypdfium2" is BSD-3-Clause/Apache-2.0 and
# verified to render; "none" disables rasterization and fails PDFs needing it.
_VALID_PDF_RASTERIZERS = {"pypdfium2", "none"}
# OCR providers. "none" fails explicitly when OCR is requested; "trocr" is the
# real local handwriting model; "fake" is a deterministic test double that is
# refused in production (see ``load_settings``).
_VALID_OCR_PROVIDERS = {"none", "fake", "trocr"}
# Inference devices. "auto" resolves to CUDA when actually available, else CPU.
_VALID_OCR_DEVICES = {"auto", "cpu", "cuda"}


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _env_int(name: str, default: int, minimum: int = 0, maximum: Optional[int] = None) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ConfigurationError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum or (maximum is not None and value > maximum):
        raise ConfigurationError(
            f"{name} must be between {minimum} and {maximum}, got {value}"
        )
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _host_set(raw: Optional[str]) -> frozenset[str]:
    if not raw:
        return frozenset()
    hosts = set()
    for item in raw.split(","):
        host = item.strip().lower().rstrip(".")
        if host:
            hosts.add(host)
    return frozenset(hosts)


def _env_set(name: str, default: str) -> frozenset[str]:
    """Parse a comma-separated list of lowercase tokens (e.g. allowed formats)."""
    raw = _env(name) or default
    tokens = {token.strip().lower() for token in raw.split(",") if token.strip()}
    if not tokens:
        raise ConfigurationError(f"{name} must contain at least one value")
    return frozenset(tokens)


@dataclass(frozen=True)
class Settings:
    environment: str
    log_level: str
    database_url: str
    test_database_url: str
    secret_key: str
    access_token_ttl_seconds: int
    refresh_token_ttl_seconds: int
    token_audience: str
    argon2_time_cost: int
    argon2_memory_cost: int
    argon2_parallelism: int
    retrieval_allowed_hosts: frozenset[str]
    retrieval_max_bytes: int
    retrieval_timeout_seconds: int
    retrieval_max_redirects: int
    retrieval_respect_robots: bool
    retrieval_user_agent: str
    llm_provider: str
    storage_backend: str
    storage_local_dir: Path
    storage_bucket: Optional[str]
    job_max_attempts: int
    job_poll_interval_seconds: float
    # How long a claimed (running) job may go without progress before it is
    # considered abandoned and reclaimed. Bounds crash-recovery latency.
    job_lease_seconds: int
    # --- Document ingestion (uploads) -------------------------------------
    upload_max_bytes: int
    upload_max_pages: int
    upload_max_image_pixels: int
    upload_max_image_dimension: int
    upload_allowed_formats: frozenset[str]
    upload_max_per_user: int
    # --- Document preprocessing (Checkpoint C) ----------------------------
    # PDF rasterization backend. "pypdfium2" is the only verified backend;
    # "none" fails PDFs that require rasterization with a clear error.
    pdf_rasterizer: str
    # Rasterization scale (multiplies a 72-DPI PDF point to get pixels).
    pdf_render_scale: float
    # Hard ceiling on a single rendered page's pixel dimension.
    pdf_max_page_dimension: int
    # Working-image normalization target.
    preprocess_max_working_dimension: int
    # Per-page wall-clock budget; exceeding it fails that page.
    preprocess_page_timeout_seconds: float
    # Whole-job wall-clock budget; exceeding it stops before the next page.
    preprocess_job_timeout_seconds: float
    # Ceiling on total pages processed in one job (defends against huge PDFs).
    preprocess_max_total_pages: int
    # Bound on concurrently-running preprocessing jobs (worker-side semaphore).
    preprocess_max_concurrency: int
    # Directory for attempt-scoped temp files; empty -> system temp dir.
    preprocess_temp_dir: str
    # --- OCR (Checkpoint D) ----------------------------------------------
    # Selected OCR provider. "none" (default) fails OCR explicitly rather than
    # inventing text; "trocr" runs real local handwriting inference; "fake" is
    # a deterministic test double that is refused in production.
    ocr_provider: str
    # Model identifier or local directory. Resolved lazily at first inference.
    ocr_model_id: str
    # Inference device. "auto" picks CUDA only when actually available.
    ocr_device: str
    # Optional HF cache override; empty -> the library default cache.
    ocr_model_cache_dir: str
    # When true, the adapter never downloads: it only uses an already-cached
    # model, so a missing artifact is a fast, explicit capability error.
    ocr_local_files_only: bool
    # Per-page wall-clock budget for the whole OCR stage (segmentation +
    # inference). Exceeding it fails the page.
    ocr_page_timeout_seconds: float
    # Whole-job wall-clock budget for the OCR stage.
    ocr_job_timeout_seconds: float
    # Bound on concurrently-running OCR jobs (worker-side semaphore).
    ocr_max_concurrency: int
    # Ceiling on text regions recognized in one page (bounds cost/latency).
    ocr_max_regions_per_page: int
    # Hard ceiling on a single crop fed to the model (bounds memory).
    ocr_max_crop_dimension: int
    # Minimum ink density for a projection band to count as a text line; guards
    # against treating ruled lines/noise as text.
    ocr_min_line_ink_ratio: float
    # Opt-in switch for the real-model smoke test; ordinary CI never loads a
    # model or downloads artifacts.
    ocr_real_model_smoke_enabled: bool
    # --- document segmentation (Checkpoint E) -----------------------------
    # Version tag stamped on every segment produced by the current rules.
    # Bumping it re-tags machine segments on the next run while preserving
    # reviewer-approved rows (see DocumentSegment docstring).
    segment_algorithm_version: str
    # Safety valve on pages visited by one segmentation job (defensive; the
    # ingest/preprocess page caps already bound normal documents).
    segment_max_pages_per_job: int
    # Ceiling on OCR regions considered for one document (bounds CPU/memory).
    segment_max_regions_per_document: int
    # Ceiling on total candidate segments persisted for one document.
    segment_max_segments_per_document: int
    # Hard cap on how much of a single region's text is fed to the rules.
    segment_max_region_text_length: int
    # Whole-job wall-clock budget for the segmentation stage.
    segment_job_timeout_seconds: float

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"


def load_settings() -> Settings:
    environment = (_env("ENVIRONMENT") or "development").strip().lower()
    if environment not in _VALID_ENVIRONMENTS:
        raise ConfigurationError(
            f"ENVIRONMENT must be one of {sorted(_VALID_ENVIRONMENTS)}, got {environment!r}"
        )

    secret_key = _env("SECRET_KEY") or ""
    if environment == "production":
        if secret_key in _PLACEHOLDER_SECRETS:
            raise ConfigurationError(
                "SECRET_KEY must be set to a long random value in production."
            )
        if len(secret_key) < 32:
            raise ConfigurationError("SECRET_KEY must be at least 32 characters in production.")

    database_url = _env("DATABASE_URL")
    if not database_url:
        default_db = (
            "sqlite:///./data/unmist_test.db"
            if environment == "test"
            else "sqlite:///./data/unmist.db"
        )
        database_url = default_db

    storage_local = Path(_env("STORAGE_LOCAL_DIR") or "./data/storage")

    # Validate preprocessing settings at startup so a bad deploy fails fast
    # instead of failing at request/job time.
    pdf_rasterizer = (_env("PDF_RASTERIZER") or "pypdfium2").strip().lower()
    if pdf_rasterizer not in _VALID_PDF_RASTERIZERS:
        raise ConfigurationError(
            f"PDF_RASTERIZER must be one of {sorted(_VALID_PDF_RASTERIZERS)}, "
            f"got {pdf_rasterizer!r}"
        )
    pdf_render_scale = float(_env("PDF_RENDER_SCALE") or "2.0")
    if not 0.1 <= pdf_render_scale <= 8.0:
        raise ConfigurationError("PDF_RENDER_SCALE must be between 0.1 and 8.0")
    page_timeout = float(_env("PREPROCESS_PAGE_TIMEOUT_SECONDS") or "60.0")
    if page_timeout <= 0:
        raise ConfigurationError("PREPROCESS_PAGE_TIMEOUT_SECONDS must be positive")
    job_timeout = float(_env("PREPROCESS_JOB_TIMEOUT_SECONDS") or "600.0")
    if job_timeout <= 0:
        raise ConfigurationError("PREPROCESS_JOB_TIMEOUT_SECONDS must be positive")

    # --- OCR (Checkpoint D) -------------------------------------------------
    # The fake provider is a test double. Silently selecting it in production
    # would fabricate recognition results, so it is refused outright rather
    # than warned about.
    ocr_provider = (_env("OCR_PROVIDER") or "none").strip().lower()
    if ocr_provider not in _VALID_OCR_PROVIDERS:
        raise ConfigurationError(
            f"OCR_PROVIDER must be one of {sorted(_VALID_OCR_PROVIDERS)}, got {ocr_provider!r}"
        )
    if ocr_provider == "fake" and environment == "production":
        raise ConfigurationError(
            "OCR_PROVIDER=fake is a test double and cannot be used in production."
        )
    ocr_device = (_env("OCR_DEVICE") or "auto").strip().lower()
    if ocr_device not in _VALID_OCR_DEVICES:
        raise ConfigurationError(
            f"OCR_DEVICE must be one of {sorted(_VALID_OCR_DEVICES)}, got {ocr_device!r}"
        )
    ocr_page_timeout = float(_env("OCR_PAGE_TIMEOUT_SECONDS") or "120.0")
    if ocr_page_timeout <= 0:
        raise ConfigurationError("OCR_PAGE_TIMEOUT_SECONDS must be positive")
    ocr_job_timeout = float(_env("OCR_JOB_TIMEOUT_SECONDS") or "1800.0")
    if ocr_job_timeout <= 0:
        raise ConfigurationError("OCR_JOB_TIMEOUT_SECONDS must be positive")
    ocr_min_line_ink = float(_env("OCR_MIN_LINE_INK_RATIO") or "0.008")
    if not 0.0 <= ocr_min_line_ink <= 1.0:
        raise ConfigurationError("OCR_MIN_LINE_INK_RATIO must be within [0, 1]")
    segment_job_timeout = float(_env("SEGMENT_JOB_TIMEOUT_SECONDS") or "300.0")
    if segment_job_timeout <= 0:
        raise ConfigurationError("SEGMENT_JOB_TIMEOUT_SECONDS must be positive")

    return Settings(
        environment=environment,
        log_level=(_env("LOG_LEVEL") or "INFO").upper(),
        database_url=database_url,
        test_database_url=_env("TEST_DATABASE_URL") or "sqlite:///./data/unmist_test.db",
        secret_key=secret_key or "insecure-development-secret-do-not-use-in-production",
        access_token_ttl_seconds=_env_int("ACCESS_TOKEN_TTL_SECONDS", 900, 60, 86400),
        refresh_token_ttl_seconds=_env_int(
            "REFRESH_TOKEN_TTL_SECONDS", 604800, 60, 31536000
        ),
        token_audience=_env("TOKEN_AUDIENCE") or "unmist-api",
        argon2_time_cost=_env_int("ARGON2_TIME_COST", 3, 1, 16),
        argon2_memory_cost=_env_int("ARGON2_MEMORY_COST", 65536, 1024, 1048576),
        argon2_parallelism=_env_int("ARGON2_PARALLELISM", 4, 1, 16),
        retrieval_allowed_hosts=_host_set(_env("RETRIEVAL_ALLOWED_HOSTS")),
        retrieval_max_bytes=_env_int("RETRIEVAL_MAX_BYTES", 10 * 1024 * 1024, 1024),
        retrieval_timeout_seconds=_env_int("RETRIEVAL_TIMEOUT_SECONDS", 20, 1, 300),
        retrieval_max_redirects=_env_int("RETRIEVAL_MAX_REDIRECTS", 3, 0, 10),
        retrieval_respect_robots=_env_bool("RETRIEVAL_RESPECT_ROBOTS", True),
        retrieval_user_agent=_env("RETRIEVAL_USER_AGENT") or "UnmistBot/0.1",
        llm_provider=(_env("LLM_PROVIDER") or "none").strip().lower(),
        storage_backend=(_env("STORAGE_BACKEND") or "local").strip().lower(),
        storage_local_dir=storage_local,
        storage_bucket=_env("STORAGE_BUCKET"),
        job_max_attempts=_env_int("JOB_MAX_ATTEMPTS", 3, 1, 20),
        job_poll_interval_seconds=float(
            _env("JOB_POLL_INTERVAL_SECONDS") or "1.0"
        ),
        job_lease_seconds=_env_int("JOB_LEASE_SECONDS", 300, 5, 86400),
        upload_max_bytes=_env_int("UPLOAD_MAX_BYTES", 20 * 1024 * 1024, 1024),
        upload_max_pages=_env_int("UPLOAD_MAX_PAGES", 200, 1, 10000),
        upload_max_image_pixels=_env_int("UPLOAD_MAX_IMAGE_PIXELS", 40_000_000, 1024),
        upload_max_image_dimension=_env_int("UPLOAD_MAX_IMAGE_DIMENSION", 12000, 16),
        upload_allowed_formats=_env_set(
            "UPLOAD_ALLOWED_FORMATS", "pdf,jpg,jpeg,png,tiff"
        ),
        upload_max_per_user=_env_int("UPLOAD_MAX_PER_USER", 1000, 1),
        pdf_rasterizer=pdf_rasterizer,
        pdf_render_scale=pdf_render_scale,
        pdf_max_page_dimension=_env_int("PDF_MAX_PAGE_DIMENSION", 6000, 256),
        preprocess_max_working_dimension=_env_int(
            "PREPROCESS_MAX_WORKING_DIMENSION", 4000, 256
        ),
        preprocess_page_timeout_seconds=page_timeout,
        preprocess_job_timeout_seconds=job_timeout,
        preprocess_max_total_pages=_env_int("PREPROCESS_MAX_TOTAL_PAGES", 200, 1, 10000),
        preprocess_max_concurrency=_env_int("PREPROCESS_MAX_CONCURRENCY", 1, 1, 64),
        preprocess_temp_dir=(_env("PREPROCESS_TEMP_DIR") or "").strip(),
        ocr_provider=ocr_provider,
        ocr_model_id=(_env("OCR_MODEL_ID") or "microsoft/trocr-base-handwritten").strip(),
        ocr_device=ocr_device,
        ocr_model_cache_dir=(_env("OCR_MODEL_CACHE_DIR") or "").strip(),
        ocr_local_files_only=_env_bool("OCR_LOCAL_FILES_ONLY", False),
        ocr_page_timeout_seconds=ocr_page_timeout,
        ocr_job_timeout_seconds=ocr_job_timeout,
        ocr_max_concurrency=_env_int("OCR_MAX_CONCURRENCY", 1, 1, 64),
        ocr_max_regions_per_page=_env_int("OCR_MAX_REGIONS_PER_PAGE", 64, 1, 2000),
        ocr_max_crop_dimension=_env_int("OCR_MAX_CROP_DIMENSION", 4096, 32, 20000),
        ocr_min_line_ink_ratio=ocr_min_line_ink,
        ocr_real_model_smoke_enabled=_env_bool("OCR_REAL_MODEL_SMOKE_ENABLED", False),
        segment_algorithm_version=(
            _env("SEGMENT_ALGORITHM_VERSION") or "seg-baseline-v1"
        ).strip(),
        segment_max_pages_per_job=_env_int("SEGMENT_MAX_PAGES_PER_JOB", 500, 1, 100000),
        segment_max_regions_per_document=_env_int(
            "SEGMENT_MAX_REGIONS_PER_DOCUMENT", 20000, 1, 1_000_000
        ),
        segment_max_segments_per_document=_env_int(
            "SEGMENT_MAX_SEGMENTS_PER_DOCUMENT", 5000, 1, 250_000
        ),
        segment_max_region_text_length=_env_int(
            "SEGMENT_MAX_REGION_TEXT_LENGTH", 20000, 1, 1_000_000
        ),
        segment_job_timeout_seconds=segment_job_timeout,
    )


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Return the process-wide settings, loading them once."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def reset_settings_for_tests() -> None:
    """Clear the cached settings (used by tests)."""
    global _settings
    _settings = None
