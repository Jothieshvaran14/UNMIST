"""Knowledge profile and public portfolio API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import User
from ...schemas.profile import (
    PortfolioItemCreateRequest,
    PortfolioItemResponse,
    PortfolioSettingsResponse,
    PortfolioUpdateRequest,
    ProfileResponse,
    PublicProfileResponse,
)
from ...security.dependencies import get_current_user
from ...services.audit import record_audit
from ...services.profiles import portfolio as portfolio_service
from ...services.profiles import profile as profile_service

router = APIRouter(tags=["profiles"])


@router.get("/me/profile", response_model=ProfileResponse)
def my_profile(
    org_id: str | None = None,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return profile_service.build_profile(
        session, learner_id=actor.id, org_id=org_id
    )


# ---------------------------------------------------------------------------
# Portfolio (private controls)
# ---------------------------------------------------------------------------
@router.get("/me/portfolio/settings", response_model=PortfolioSettingsResponse)
def get_portfolio_settings(
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    settings = portfolio_service.get_or_create_settings(session, actor)
    session.commit()
    return settings


@router.patch("/me/portfolio/settings", response_model=PortfolioSettingsResponse)
def update_portfolio_settings(
    payload: PortfolioUpdateRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    settings = portfolio_service.get_or_create_settings(session, actor)
    if payload.is_public is not None:
        portfolio_service.set_public(session, settings, is_public=payload.is_public)
    if payload.headline is not None:
        settings.headline = payload.headline
    if payload.bio is not None:
        settings.bio = payload.bio
    if payload.show_assessed_only is not None:
        settings.show_assessed_only = payload.show_assessed_only
    record_audit(
        session,
        action="portfolio.settings.update",
        actor_id=actor.id,
        resource_type="portfolio_settings",
        resource_id=settings.id,
        details={"is_public": settings.is_public},
    )
    session.commit()
    return settings


@router.post("/me/portfolio/items", response_model=PortfolioItemResponse, status_code=201)
def add_portfolio_item(
    payload: PortfolioItemCreateRequest,
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    item = portfolio_service.add_item(
        session,
        user_id=actor.id,
        item_type=payload.item_type,
        title=payload.title,
        concept_id=payload.concept_id,
        evidence_summary=payload.evidence_summary,
        is_self_reported=payload.is_self_reported,
        visibility=payload.visibility,
        score=payload.score,
        max_score=payload.max_score,
    )
    record_audit(
        session,
        action="portfolio.item.add",
        actor_id=actor.id,
        resource_type="portfolio_item",
        resource_id=item.id,
        details={"visibility": item.visibility},
    )
    session.commit()
    return item


@router.get("/me/portfolio/items", response_model=list[PortfolioItemResponse])
def list_portfolio_items(
    actor: User = Depends(get_current_user),
    session: Session = Depends(get_db),
):
    return portfolio_service.list_public_items(session, actor.id)


# ---------------------------------------------------------------------------
# Public projection (no authentication; only explicitly approved fields)
# ---------------------------------------------------------------------------
@router.get("/portfolios/{public_id}", response_model=PublicProfileResponse)
def get_public_profile(public_id: str, session: Session = Depends(get_db)):
    profile = portfolio_service.get_public_profile(session, public_id)
    return PublicProfileResponse(**profile)
