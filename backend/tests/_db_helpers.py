"""Shared DB test helpers."""

from __future__ import annotations

from app.models.user import User


def make_user(session, email="learner@example.com", password_hash="x") -> User:
    """Create a real user so FK constraints are satisfiable in tests."""
    user = User(email=email, password_hash=password_hash, display_name="Learner")
    session.add(user)
    session.flush()
    return user
