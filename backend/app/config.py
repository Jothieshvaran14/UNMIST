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
