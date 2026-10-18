"""Opt-in public portfolio.

Public views are *projections* of private records and expose only fields the
user explicitly approved. Private assessment attempts, reviewer comments,
institutional information, and unapproved items are never exposed.

Public proficiency claims distinguish self-reported skills from assessed
evidence and explain what each label does and does not prove.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...errors import NotFoundError, ValidationError
from ...models.portfolio import PortfolioItem, PortfolioSetting
from ...models.user import User

VERIFICATION_NOTE = (
    "Assessed evidence reflects automated, rubric-based evaluation and may "
    "require human review. It is not an accredited certification."
)
SELF_REPORTED_NOTE = "Self-reported by the learner; not independently assessed."


def get_or_create_settings(session: Session, user: User) -> PortfolioSetting:
    settings = session.execute(
        select(PortfolioSetting).where(PortfolioSetting.user_id == user.id)
    ).scalar_one_or_none()
    if settings is None:
        settings = PortfolioSetting(
            user_id=user.id,
            public_id=secrets.token_urlsafe(16).replace("-", "").replace("_", "")[:24],
            is_public=False,
            show_assessed_only=True,
        )
        session.add(settings)
        session.flush()
    return settings


def set_public(
    session: Session, settings: PortfolioSetting, *, is_public: bool
) -> PortfolioSetting:
    if is_public and not settings.public_id:
        raise ValidationError("Portfolio has no public identifier")
    settings.is_public = is_public
    settings.published_at = datetime.now(timezone.utc) if is_public else None
    session.flush()
    return settings


def add_item(
    session: Session,
    *,
    user_id: str,
    item_type: str,
    title: str,
    concept_id: Optional[str] = None,
    evidence_summary: Optional[str] = None,
    is_self_reported: bool = False,
    visibility: str = "private",
    score: Optional[float] = None,
    max_score: Optional[float] = None,
) -> PortfolioItem:
    if visibility not in {"private", "public"}:
        raise ValidationError("visibility must be 'private' or 'public'")
    item = PortfolioItem(
        user_id=user_id,
        concept_id=concept_id,
        item_type=item_type,
        title=title,
        evidence_summary=evidence_summary,
        is_self_reported=is_self_reported,
        visibility=visibility,
        score=score,
        max_score=max_score,
    )
    session.add(item)
    session.flush()
    return item


def get_public_profile(session: Session, public_id: str) -> dict:
    settings = session.execute(
        select(PortfolioSetting).where(PortfolioSetting.public_id == public_id)
    ).scalar_one_or_none()
    if settings is None or not settings.is_public:
        # Do not reveal whether an identifier exists.
        raise NotFoundError("Public profile not found")

    items_query = select(PortfolioItem).where(
        PortfolioItem.user_id == settings.user_id,
        PortfolioItem.visibility == "public",
    )
    items = list(session.execute(items_query).scalars().all())
    if settings.show_assessed_only:
        items = [item for item in items if not item.is_self_reported]

    public_items = []
    for item in items:
        entry: dict[str, Any] = {
            "item_type": item.item_type,
            "title": item.title,
            "evidence_summary": item.evidence_summary,
            "is_self_reported": item.is_self_reported,
            "verification_note": (
                SELF_REPORTED_NOTE if item.is_self_reported else VERIFICATION_NOTE
            ),
        }
        if item.score is not None:
            entry["score"] = item.score
            entry["max_score"] = item.max_score
        public_items.append(entry)

    return {
        "public_id": settings.public_id,
        "headline": settings.headline,
        "bio": settings.bio,
        "items": public_items,
        "limitations": (
            "This portfolio shows only what the learner chose to publish. It does not "
            "include private assessments, institutional records, or unreviewed results."
        ),
    }


def list_public_items(session: Session, user_id: str) -> list[PortfolioItem]:
    return list(
        session.execute(
            select(PortfolioItem)
            .where(PortfolioItem.user_id == user_id)
            .order_by(PortfolioItem.created_at.desc())
        ).scalars().all()
    )
