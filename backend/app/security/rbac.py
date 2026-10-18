"""Role-based access control helpers."""

from __future__ import annotations

# Higher rank = more privilege. Owners manage the tenant; admins manage
# organization settings; faculty author and review assessments; members are
# general collaborators; students are assessed learners.
ROLE_RANK: dict[str, int] = {
    "student": 1,
    "member": 2,
    "faculty": 3,
    "admin": 4,
    "owner": 5,
}


def role_at_least(role: str, minimum_role: str) -> bool:
    """Whether ``role`` satisfies the ``minimum_role`` requirement."""
    return ROLE_RANK.get(role, 0) >= ROLE_RANK.get(minimum_role, 0)


def is_privileged(role: str) -> bool:
    """Roles allowed to perform administrative and review actions."""
    return role_at_least(role, "admin")


def is_author(role: str) -> bool:
    """Roles allowed to author assessments and rubrics."""
    return role_at_least(role, "faculty")
