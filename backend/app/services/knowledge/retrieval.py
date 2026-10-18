"""Lawful, policy-constrained outbound retrieval.

Security controls implemented here:

  * Scheme allowlist (http/https only).
  * Host allowlist; retrieval is disabled entirely when the allowlist is empty.
  * SSRF defense: destination IPs are resolved and rejected if they are private,
    loopback, link-local, reserved, multicast, or otherwise non-global. Every
    redirect hop is re-validated.
  * robots.txt is respected when enabled (cached per host).
  * Response size, connection/read timeouts, and redirect count are bounded.
  * No authentication, cookies, or credentials are attached to outbound calls.

This client never bypasses access controls, CAPTCHAs, or paywalls, and never
disguises the User-Agent.
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

from ...config import Settings, get_settings
from ...errors import PolicyError, UpstreamError


class RetrievalError(PolicyError):
    """Base class for retrieval failures that are policy or upstream related."""


class DisallowedHostError(RetrievalError):
    def __init__(self, host: str) -> None:
        super().__init__(
            f"Retrieval host '{host}' is not permitted by policy", code="host_not_allowed"
        )


class PrivateAddressError(RetrievalError):
    def __init__(self, host: str) -> None:
        super().__init__(
            f"Retrieval host '{host}' resolves to a non-public address", code="ssrf_blocked"
        )


class BlockedByRobotsError(RetrievalError):
    def __init__(self, url: str) -> None:
        super().__init__(f"robots.txt disallows retrieval of '{url}'", code="robots_disallowed")


class PayloadTooLargeError(RetrievalError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"Response exceeded the {limit}-byte limit", code="payload_too_large")


class RedirectLimitError(RetrievalError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"Too many redirects (limit {limit})", code="too_many_redirects")


@dataclass(frozen=True)
class RetrievedDocument:
    url: str
    final_url: str
    status_code: int
    content_type: str
    content: bytes
    fetched_at: datetime
    content_hash: str
    charset: str = "utf-8"


# ---------------------------------------------------------------------------
# robots.txt parsing (self-contained; no deprecated stdlib dependency)
# ---------------------------------------------------------------------------
def parse_robots_txt(text: str) -> list[tuple[list[str], list[tuple[bool, str]]]]:
    """Parse robots.txt into ``[(user_agents, [(allow, path)])]`` groups."""
    groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
    agents: list[str] = []
    rules: list[tuple[bool, str]] = []

    def flush() -> None:
        if agents or rules:
            groups.append((list(agents), list(rules)))

    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        field, _, value = line.partition(":")
        field = field.strip().lower()
        value = value.strip()
        if field == "user-agent":
            if rules:
                flush()
                agents, rules = [], []
            if value:
                agents.append(value.lower())
        elif field in {"allow", "disallow"}:
            rules.append((field == "allow", value))
    flush()
    return groups


def robots_allows(groups, user_agent: str, path: str) -> bool:
    """Return whether ``path`` is allowed for ``user_agent``."""
    agent = (user_agent or "*").lower()
    best: Optional[list[tuple[bool, str]]] = None
    best_len = -1
    for agents, rules in groups:
        matched = False
        for candidate in agents:
            if candidate == "*" or candidate in agent:
                matched = True
                break
        if matched:
            # Prefer the most specific (longest) matching rule list.
            if len(rules) >= best_len:
                best = rules
                best_len = len(rules)
    if best is None:
        return True

    allowed = True
    best_rule_len = -1
    for allow, pattern in best:
        if pattern == "":
            continue
        if path.startswith(pattern) and len(pattern) > best_rule_len:
            allowed = allow
            best_rule_len = len(pattern)
    return allowed


def is_public_ip(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_global
    except ValueError:
        return False


def _resolve_and_check(host: str) -> list[str]:
    """Resolve ``host`` and ensure every address is public (SSRF defense)."""
    try:
        parsed = ipaddress.ip_address(host)
        if not parsed.is_global:
            raise PrivateAddressError(host)
        return [str(parsed)]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UpstreamError(f"Could not resolve host '{host}'") from exc

    addresses = sorted({str(info[4][0]) for info in infos})
    if not addresses:
        raise UpstreamError(f"Host '{host}' did not resolve to any address")
    for address in addresses:
        if not is_public_ip(address):
            raise PrivateAddressError(host)
    return addresses


class RobotsChecker:
    """Small, cached robots.txt checker."""

    def __init__(self, user_agent: str, timeout: float, max_bytes: int) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_bytes = max_bytes
        self._cache: dict[str, Optional[list]] = {}

    def _fetch(self, host: str, scheme: str) -> Optional[list]:
        if host in self._cache:
            return self._cache[host]
        groups: Optional[list] = None
        url = f"{scheme}://{host}/robots.txt"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(self.timeout), follow_redirects=False
            ) as client:
                response = client.get(url, headers={"User-Agent": self.user_agent})
                if response.status_code == 200 and len(response.content) <= self.max_bytes:
                    groups = parse_robots_txt(response.text)
        except Exception:
            groups = None
        self._cache[host] = groups
        return groups

    def is_allowed(self, url: str, scheme: str) -> bool:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        groups = self._fetch(host, scheme)
        if groups is None:
            # Unavailable robots.txt: allow, but we record nothing here.
            return True
        return robots_allows(groups, self.user_agent, parsed.path or "/")


class RetrievalClient:
    """HTTP client constrained by the retrieval policy."""

    def __init__(
        self,
        settings: Settings | None = None,
        transport: httpx.BaseTransport | None = None,
        resolver=None,
    ):
        self.settings = settings or get_settings()
        self._transport = transport
        # ``resolver`` allows tests (and alternative DNS setups) to supply
        # resolution without performing real network lookups.
        self._resolver = resolver
        self.robots = RobotsChecker(
            self.settings.retrieval_user_agent,
            timeout=float(self.settings.retrieval_timeout_seconds),
            max_bytes=self.settings.retrieval_max_bytes,
        )

    def is_host_allowed(self, host: str) -> bool:
        if not self.settings.retrieval_allowed_hosts:
            return False
        return host.strip().lower() in self.settings.retrieval_allowed_hosts

    def _validate_url(self, url: str) -> tuple[str, str]:
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        if scheme not in {"http", "https"}:
            raise RetrievalError(
                f"URL scheme '{scheme}' is not permitted", code="scheme_not_allowed"
            )
        if not host:
            raise RetrievalError("URL has no host", code="invalid_url")
        if not self.is_host_allowed(host):
            raise DisallowedHostError(host)
        return scheme, host

    def fetch(self, url: str) -> RetrievedDocument:
        scheme, host = self._validate_url(url)
        if self.settings.retrieval_respect_robots and not self.robots.is_allowed(url, scheme):
            raise BlockedByRobotsError(url)

        current_url = url
        redirects = 0
        while True:
            current_scheme, current_host = self._validate_url(current_url)
            self._resolve(current_host)
            if self.settings.retrieval_respect_robots and not self.robots.is_allowed(
                current_url, current_scheme
            ):
                raise BlockedByRobotsError(current_url)

            try:
                status_code, headers, content = self._request(current_url)
            except httpx.TimeoutException as exc:
                raise UpstreamError(f"Retrieval timed out for '{current_url}'") from exc
            except httpx.HTTPError as exc:
                raise UpstreamError(f"Retrieval failed for '{current_url}'") from exc

            if status_code in {301, 302, 303, 307, 308} and "location" in headers:
                redirects += 1
                if redirects > self.settings.retrieval_max_redirects:
                    raise RedirectLimitError(self.settings.retrieval_max_redirects)
                current_url = urljoin(current_url, headers["location"])
                self._validate_url(current_url)  # re-check scheme/host on redirect
                continue

            if status_code >= 400:
                raise UpstreamError(
                    f"Upstream returned HTTP {status_code} for '{current_url}'"
                )
            if len(content) > self.settings.retrieval_max_bytes:
                raise PayloadTooLargeError(self.settings.retrieval_max_bytes)

            return RetrievedDocument(
                url=url,
                final_url=current_url,
                status_code=status_code,
                content_type=headers.get("content-type", "application/octet-stream"),
                content=content,
                fetched_at=datetime.now(timezone.utc),
                content_hash=hashlib.sha256(content).hexdigest(),
                charset=self._charset_from(headers.get("content-type", "")),
            )

    def _resolve(self, host: str) -> list[str]:
        if self._resolver is not None:
            addresses = self._resolver(host)
            if not addresses:
                raise UpstreamError(f"Resolver returned no address for '{host}'")
            return addresses
        return _resolve_and_check(host)

    def _request(self, url: str) -> tuple[int, dict[str, str], bytes]:
        headers = {"User-Agent": self.settings.retrieval_user_agent, "Accept": "*/*"}
        with httpx.Client(
            timeout=httpx.Timeout(float(self.settings.retrieval_timeout_seconds)),
            follow_redirects=False,
            headers=headers,
            transport=self._transport,
        ) as client:
            with client.stream("GET", url) as response:
                status_code = response.status_code
                response_headers = dict(response.headers)
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > self.settings.retrieval_max_bytes:
                        raise PayloadTooLargeError(self.settings.retrieval_max_bytes)
                    chunks.append(chunk)
                return status_code, response_headers, b"".join(chunks)

    @staticmethod
    def _charset_from(content_type: str) -> str:
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                return part.split("=", 1)[1].strip().strip('"')
        return "utf-8"
