"""Common Pydantic schemas."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int

    @classmethod
    def create(
        cls, items: list[T], total: int, limit: int, offset: int
    ) -> "Page[T]":
        return cls(items=items, total=total, limit=limit, offset=offset)


class MessageResponse(BaseModel):
    message: str
