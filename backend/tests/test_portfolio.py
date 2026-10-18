"""Public portfolio privacy tests."""

from __future__ import annotations

from app.errors import NotFoundError
from app.models import User
from app.services.profiles import portfolio


def _user(session, email="learner@example.com"):
    user = User(email=email, password_hash="x", display_name="Learner")
    session.add(user)
    session.flush()
    return user


def test_portfolio_is_private_by_default(db_session):
    user = _user(db_session)
    settings = portfolio.get_or_create_settings(db_session, user)
    assert settings.is_public is False
    assert settings.show_assessed_only is True
    assert settings.public_id


def test_public_profile_excludes_private_items(db_session):
    user = _user(db_session)
    settings = portfolio.get_or_create_settings(db_session, user)
    portfolio.add_item(
        db_session, user_id=user.id, item_type="skill", title="Public Skill",
        visibility="public", is_self_reported=False,
    )
    portfolio.add_item(
        db_session, user_id=user.id, item_type="skill", title="Secret Skill",
        visibility="private", is_self_reported=False,
    )
    portfolio.set_public(db_session, settings, is_public=True)
    db_session.flush()

    profile = portfolio.get_public_profile(db_session, settings.public_id)
    titles = {item["title"] for item in profile["items"]}
    assert titles == {"Public Skill"}


def test_show_assessed_only_excludes_self_reported(db_session):
    user = _user(db_session)
    settings = portfolio.get_or_create_settings(db_session, user)
    portfolio.add_item(
        db_session, user_id=user.id, item_type="skill", title="Assessed",
        visibility="public", is_self_reported=False,
    )
    portfolio.add_item(
        db_session, user_id=user.id, item_type="skill", title="Claimed",
        visibility="public", is_self_reported=True,
    )
    portfolio.set_public(db_session, settings, is_public=True)
    db_session.flush()

    profile = portfolio.get_public_profile(db_session, settings.public_id)
    titles = {item["title"] for item in profile["items"]}
    assert "Assessed" in titles
    assert "Claimed" not in titles


def test_unknown_public_id_is_not_found(db_session):
    import pytest

    with pytest.raises(NotFoundError):
        portfolio.get_public_profile(db_session, "does-not-exist")


def test_non_public_portfolio_is_not_found(db_session):
    import pytest

    user = _user(db_session)
    settings = portfolio.get_or_create_settings(db_session, user)
    with pytest.raises(NotFoundError):
        portfolio.get_public_profile(db_session, settings.public_id)
