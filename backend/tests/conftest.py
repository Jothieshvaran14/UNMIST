"""Pytest fixtures for the Unmist test suite.

Tests run against SQLite by default so the suite has no external service
dependencies. PostgreSQL-specific behaviour is covered by integration tests
that opt into a real database via ``UNMIST_TEST_DATABASE_URL``.
"""

from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("TEST_DATABASE_URL", "sqlite:///./data/unmist_test.db")
os.environ.setdefault(
    "SECRET_KEY", "test-secret-key-that-is-long-enough-0123456789abcdef"
)
os.environ.setdefault("RETRIEVAL_ALLOWED_HOSTS", "")

import pytest  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.db import Base, build_engine  # noqa: E402
from app.main import create_app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


@pytest.fixture()
def engine():
    settings = load_settings()
    engine = build_engine(settings, for_tests=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def db_session(session_factory):
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def client(session_factory):
    from app.db import get_db

    app = create_app()

    def override_get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def registered_user(client):
    """Create and return a user with a valid access token."""
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )
    assert response.status_code == 201, response.text
    user = response.json()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )
    assert login.status_code == 200, login.text
    tokens = login.json()
    return {"user": user, "tokens": tokens}


@pytest.fixture()
def auth_headers(registered_user):
    return {"Authorization": f"Bearer {registered_user['tokens']['access_token']}"}
