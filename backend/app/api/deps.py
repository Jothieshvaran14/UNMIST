"""Shared API dependencies (pagination, request context)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Request


@dataclass(frozen=True)
class Pagination:
    limit: int
    offset: int

    @classmethod
    def from_request(cls, limit: int = 20, offset: int = 0) -> "Pagination":
        return cls(limit=max(1, min(limit, 100)), offset=max(0, offset))


def pagination(
    limit: int = 20,
    offset: int = 0,
) -> Pagination:
    return Pagination.from_request(limit=limit, offset=offset)


def client_ip(request: Request) -> Optional[str]:
    """Best-effort client IP, honouring a single trusted proxy header."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # Take the first (client) entry; the value is trusted only at the edge.
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


def request_id(request: Request) -> Optional[str]:
    return request.headers.get("x-request-id")
