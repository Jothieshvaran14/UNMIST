"""Audit logging service.

Sensitive keys are redacted before storage. Audit events are written in the
same transaction as the action they describe where possible.
"""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from ..models import AuditEvent

_SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "token",
    "refresh_token",
    "access_token",
    "secret",
    "api_key",
    "authorization",
    "cookie",
    "ssn",
    "aadhaar",
    "phone",
}


def redact(details: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Return a copy of ``details`` with sensitive values removed."""
    if details is None:
        return None
    cleaned: dict[str, Any] = {}
    for key, value in details.items():
        normalized = key.lower()
        if any(sensitive in normalized for sensitive in _SENSITIVE_KEYS):
            cleaned[key] = "[redacted]"
        elif isinstance(value, dict):
            cleaned[key] = redact(value)
        else:
            cleaned[key] = value
    return cleaned


def record_audit(
    session: Session,
    *,
    action: str,
    actor_id: Optional[str] = None,
    org_id: Optional[str] = None,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    outcome: str = "success",
    details: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_id=actor_id,
        org_id=org_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        details=redact(details),
        ip_address=ip_address,
    )
    session.add(event)
    return event
