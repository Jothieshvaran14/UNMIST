"""Authentication and account endpoint tests."""

from __future__ import annotations


def test_health_is_public(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_register_returns_user_without_password(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "bob@example.com", "password": "correct-horse-battery"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "bob@example.com"
    assert "password" not in body
    assert "password_hash" not in body


def test_register_rejects_duplicate_email(client, registered_user):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )
    assert response.status_code == 409


def test_register_rejects_weak_password(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "weak@example.com", "password": "short"},
    )
    assert response.status_code == 422


def test_login_success_returns_tokens(client, registered_user):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["access_token"]
    assert body["refresh_token"]


def test_login_wrong_password_is_rejected(client, registered_user):
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "wrong-password-here"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_error"


def test_me_requires_authentication(client):
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401


def test_me_returns_profile(client, auth_headers, registered_user):
    response = client.get("/api/v1/auth/me", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == registered_user["user"]["id"]
    assert body["email"] == "alice@example.com"


def test_refresh_rotates_tokens(client, registered_user):
    old_refresh = registered_user["tokens"]["refresh_token"]
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert response.status_code == 200
    new_refresh = response.json()["refresh_token"]
    assert new_refresh != old_refresh
    # The old refresh token must no longer be usable (rotation).
    reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse.status_code == 401


def test_logout_revokes_refresh_tokens(client, registered_user):
    refresh = registered_user["tokens"]["refresh_token"]
    response = client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {registered_user['tokens']['access_token']}"},
    )
    assert response.status_code == 204
    reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert reuse.status_code == 401
