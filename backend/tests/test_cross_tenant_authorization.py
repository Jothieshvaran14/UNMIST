"""Checkpoint F: cross-tenant authorization regression tests.

These lock down two P0 defects found during the Checkpoint F integration audit
and fixed in the same checkpoint. Both were verified to reproduce before the
fix and to be closed after it, so they are written as ordinary pass-today
regression tests.

P0-1 — ``GET /api/v1/topics`` returned *every* user's personal topics.
``graph_service.list_topics`` filtered only on ``org_id IS NULL``, and
``create_topic`` hardcodes ``org_id=None``, so that filter matched every
personal topic in the database regardless of owner. The listing is now scoped
through the same rule that guards single-topic reads.

P0-2 — ``GET /api/v1/questions/{id}`` performed a bare ``session.get`` with no
object-level authorization, returning another tenant's question including
``reference_answer`` (the grading key). ``/questions/{id}/rubrics`` (GET and
POST) and ``POST /assessments`` shared the gap. Questions now resolve through
their topic's authorization rule, so all four routes enforce one policy.
"""

from __future__ import annotations


def _register(client, email: str) -> dict:
    """Register + log in, returning auth headers for a fresh tenant."""
    password = "a-strong-enough-pass"
    created = client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert created.status_code == 201, created.text
    login = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _topic(client, headers, name="Private topic") -> str:
    created = client.post("/api/v1/topics", json={"name": name}, headers=headers)
    assert created.status_code == 201, created.text
    return created.json()["id"]


# ---------------------------------------------------------------------------
# P0-1: topic listing is scoped to the caller
# ---------------------------------------------------------------------------
def test_topic_listing_does_not_leak_other_users_personal_topics(client):
    alice = _register(client, "alice.topics@example.com")
    bob = _register(client, "bob.topics@example.com")

    private_id = _topic(client, alice, "Alice private research")
    assert private_id

    listed = client.get("/api/v1/topics", headers=bob)
    assert listed.status_code == 200, listed.text
    returned_ids = [item["id"] for item in listed.json()["items"]]
    assert private_id not in returned_ids, (
        f"user B's topic listing contained user A's personal topic {private_id}"
    )


def test_topic_listing_still_returns_your_own_topics(client):
    """The fix narrows the query without hiding the caller's own topics."""
    alice = _register(client, "alice.owns@example.com")
    bob = _register(client, "bob.owns@example.com")

    mine = _topic(client, alice, "Mine")
    _topic(client, bob, "Not mine")

    listed = client.get("/api/v1/topics", headers=alice)
    assert listed.status_code == 200, listed.text
    returned_ids = [item["id"] for item in listed.json()["items"]]
    assert returned_ids == [mine], (
        f"expected the caller's own topics only, got {returned_ids}"
    )


def test_topic_listing_total_is_scoped_not_global(client):
    """The pagination total must count only the caller's visible topics."""
    alice = _register(client, "alice.total@example.com")
    bob = _register(client, "bob.total@example.com")

    for index in range(3):
        _topic(client, alice, f"Alice {index}")
    for index in range(5):
        _topic(client, bob, f"Bob {index}")

    listed = client.get("/api/v1/topics", headers=alice)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["total"] == 3, f"expected 3 visible topics, got {body['total']}"
    assert len(body["items"]) == 3


# ---------------------------------------------------------------------------
# P0-2: question/assessment routes enforce object-level authorization
# ---------------------------------------------------------------------------
def _question(client, headers, topic_id: str, org_id: str) -> str:
    created = client.post(
        "/api/v1/questions",
        json={
            "topic_id": topic_id,
            "org_id": org_id,
            "prompt": "What is 2+2?",
            "reference_answer": "the grading key: 4",
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _authorised_question(client) -> tuple[dict, dict, str, str]:
    """An author (org owner) with one personal topic and one question."""
    author = _register(client, "author.q@example.com")
    outsider = _register(client, "outsider.q@example.com")

    org = client.post("/api/v1/orgs", json={"name": "Q Org"}, headers=author)
    assert org.status_code == 201, org.text
    org_id = org.json()["id"]

    topic_id = _topic(client, author, "Author exams")
    question_id = _question(client, author, topic_id, org_id)
    return author, outsider, org_id, question_id


def test_question_read_is_denied_across_tenants(client):
    _author, outsider, _org_id, question_id = _authorised_question(client)

    read = client.get(f"/api/v1/questions/{question_id}", headers=outsider)
    assert read.status_code in (403, 404), (
        f"a non-owner read another tenant's question (status {read.status_code})"
    )
    assert "grading key" not in read.text, "the answer key was disclosed"
    assert "reference_answer" not in read.text


def test_question_rubric_read_is_denied_across_tenants(client):
    _author, outsider, _org_id, question_id = _authorised_question(client)

    read = client.get(
        f"/api/v1/questions/{question_id}/rubrics", headers=outsider
    )
    assert read.status_code in (403, 404), (
        f"a non-owner listed another tenant's rubrics (status {read.status_code})"
    )


def test_question_rubric_write_is_denied_across_tenants(client):
    author, outsider, org_id, question_id = _authorised_question(client)
    # The outsider gets their own org so ``authorize_author`` passes; only the
    # object-level check on the *question's* topic can stop this.
    other_org = client.post(
        "/api/v1/orgs", json={"name": "Outsider Org"}, headers=outsider
    )
    assert other_org.status_code == 201, other_org.text

    written = client.post(
        f"/api/v1/questions/{question_id}/rubrics",
        json={
            "org_id": other_org.json()["id"],
            "criteria": [{"id": "c1", "description": "ok", "max_points": 1}],
        },
        headers=outsider,
    )
    assert written.status_code in (403, 404), (
        f"a non-member attached a rubric to another org's question "
        f"(status {written.status_code})"
    )
    assert org_id  # the author's org was never the target


def test_attempt_cannot_be_created_on_another_tenants_question(client):
    _author, outsider, _org_id, question_id = _authorised_question(client)

    created = client.post(
        "/api/v1/assessments",
        json={"question_id": question_id},
        headers=outsider,
    )
    assert created.status_code in (403, 404), (
        f"a non-member started an attempt on another tenant's question "
        f"(status {created.status_code})"
    )


def test_owner_retains_full_access_to_their_question(client):
    """The fix must not break the legitimate owner path."""
    author, _outsider, org_id, question_id = _authorised_question(client)

    read = client.get(f"/api/v1/questions/{question_id}", headers=author)
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["prompt"] == "What is 2+2?"
    assert body["reference_answer"] == "the grading key: 4"

    rubric = client.post(
        f"/api/v1/questions/{question_id}/rubrics",
        json={
            "org_id": org_id,
            "criteria": [{"id": "c1", "description": "ok", "max_points": 1}],
        },
        headers=author,
    )
    assert rubric.status_code == 201, rubric.text

    listed = client.get(
        f"/api/v1/questions/{question_id}/rubrics", headers=author
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 1


def test_author_cannot_attach_question_to_another_users_personal_topic(client):
    """P0-2 also covered writes: topics are private to their creator."""
    owner = _register(client, "topic.owner@example.com")
    author = _register(client, "topic.author@example.com")

    foreign_topic = _topic(client, owner, "Owner's personal topic")

    org = client.post("/api/v1/orgs", json={"name": "Author Org"}, headers=author)
    assert org.status_code == 201, org.text

    created = client.post(
        "/api/v1/questions",
        json={
            "topic_id": foreign_topic,
            "org_id": org.json()["id"],
            "prompt": "Injected into someone else's topic",
        },
        headers=author,
    )
    assert created.status_code in (403, 404), (
        f"an author attached a question to another user's personal topic "
        f"(status {created.status_code})"
    )
