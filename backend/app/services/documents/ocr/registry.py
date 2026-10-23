"""OCR provider selection and lifecycle.

One shared provider instance per process. Selection is decided by validated
configuration (``OCR_PROVIDER``) at the point of first use — **never** at
application startup — so importing the app, serving ordinary requests, or
running a non-OCR test never loads a model or touches the network.

Three guarantees this module enforces:

  * The ``fake`` provider can only be reached outside production; if production
    configuration names it, provider construction raises rather than
    fabricating recognition results (defence in depth behind the startup check
    in ``app.config``).
  * Heavy imports (``torch``, ``transformers``, the TrOCR adapter) happen inside
    the factory function, so a base install without the ``ocr`` extra still
    imports the whole package and reports a clean capability error instead of
    crashing on import.
  * Instances are cached and reused, so a loaded model is not reloaded per page
    or per request; ``close_providers`` releases them at shutdown.
"""

from __future__ import annotations

import threading
from typing import Callable, Optional

from ....config import Settings, get_settings
from ....models.documents import DocumentErrorCategory
from .base import OcrError, OcrProvider

# provider name -> factory. Factories are lazy: they import heavy modules only
# when actually selected.
_FACTORIES: dict[str, Callable[[Settings], OcrProvider]] = {}

# Process-wide cache. Guarded by a lock so concurrent first use under threads
# cannot double-construct (and, for TrOCR, double-load the model).
_instance_lock = threading.Lock()
_instances: dict[str, OcrProvider] = {}


def register_provider(name: str, factory: Callable[[Settings], OcrProvider]) -> None:
    """Register a provider factory under a lowercase name."""
    _FACTORIES[name.strip().lower()] = factory


def _build_none(_settings: Settings) -> OcrProvider:
    from .none import NoneOcrProvider

    return NoneOcrProvider()


def _build_fake(_settings: Settings) -> OcrProvider:
    from .fake import FakeOcrProvider

    return FakeOcrProvider()


def _build_trocr(settings: Settings) -> OcrProvider:
    # Imported lazily: pulls in torch/transformers only when TrOCR is selected.
    from .trocr import TrocrOcrProvider

    return TrocrOcrProvider.from_settings(settings)


register_provider("none", _build_none)
register_provider("fake", _build_fake)
register_provider("trocr", _build_trocr)


def available_providers() -> list[str]:
    """Names of registered providers, for validation and diagnostics."""
    return sorted(_FACTORIES)


def get_ocr_provider(
    settings: Optional[Settings] = None,
    *,
    name: Optional[str] = None,
) -> OcrProvider:
    """Return the configured (or explicitly named) provider, constructing once.

    ``name`` overrides configuration for tests that need a specific provider
    without mutating process-wide settings.

    Raises :class:`OcrError` when the configured provider cannot be built, so
    an unavailable OCR capability surfaces as a bounded, classifiable failure
    rather than an import-time crash or a silent empty result.
    """
    cfg = settings or get_settings()
    chosen = (name or cfg.ocr_provider).strip().lower()

    # Production must never reach the test double, even if settings were
    # assembled by hand and bypassed startup validation.
    if chosen == "fake" and cfg.is_production:
        raise OcrError(
            "OCR provider 'fake' cannot be used in production",
            category=DocumentErrorCategory.ocr_unavailable.value,
            permanent=True,
        )

    factory = _FACTORIES.get(chosen)
    if factory is None:
        raise OcrError(
            f"Unknown OCR provider '{chosen}'",
            category=DocumentErrorCategory.ocr_unavailable.value,
            permanent=True,
        )

    with _instance_lock:
        instance = _instances.get(chosen)
        if instance is not None:
            return instance
        try:
            instance = factory(cfg)
        except OcrError:
            raise
        except Exception as exc:  # noqa: BLE001 - classify construction failures
            # Dependency missing, model artifact absent, bad device: all
            # capability problems. Permanent by default, because retrying
            # without changing the environment cannot fix them.
            raise OcrError(
                f"OCR provider '{chosen}' could not be initialized",
                category=DocumentErrorCategory.ocr_unavailable.value,
                permanent=True,
            ) from exc
        _instances[chosen] = instance
        return instance


def reset_ocr_providers_for_tests() -> None:
    """Close and drop every cached provider (used between tests)."""
    with _instance_lock:
        for instance in _instances.values():
            try:
                instance.close()
            except Exception:  # noqa: BLE001 - teardown must never raise
                pass
        _instances.clear()
