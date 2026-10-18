"""Organization and membership schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class OrgCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    is_institution: bool = False


class OrgResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    is_institution: bool
    created_at: datetime


class InviteRequest(BaseModel):
    email: str
    role: str = Field(pattern="^(owner|admin|member|faculty|student)$")


class MembershipResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    email: str
    display_name: Optional[str]
    role: str
    joined_at: datetime
