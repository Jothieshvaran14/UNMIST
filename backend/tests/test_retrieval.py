"""Retrieval policy and SSRF-defense tests."""

from __future__ import annotations

from dataclasses import replace

import httpx
import pytest
from app.config import load_settings
from app.errors import PolicyError
from app.services.knowledge.retrieval import (
    DisallowedHostError,
    PayloadTooLargeError,
    PrivateAddressError,
    RetrievalClient,
    parse_robots_txt,
    robots_allows,
)


class FakeTransport(httpx.BaseTransport):
    def __init__(self, handler):
        self.handler = handler

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self.handler(request)


def _settings(**overrides):
    base = load_settings()
    defaults = dict(retrieval_respect_robots=False, retrieval_allowed_hosts=frozenset())
    defaults.update(overrides)
    return replace(base, **defaults)


def _public_resolver(host: str) -> list[str]:
    """Deterministic resolver for tests (no real DNS)."""
    return ["93.184.216.34"]


def test_empty_allowlist_blocks_everything():
    client = RetrievalClient(_settings(retrieval_allowed_hosts=frozenset()))
    with pytest.raises(DisallowedHostError):
        client.fetch("https://example.com/page")


def test_disallowed_host_is_rejected():
    client = RetrievalClient(
        _settings(retrieval_allowed_hosts=frozenset({"allowed.example"}))
    )
    with pytest.raises(DisallowedHostError):
        client.fetch("https://evil.example/page")


def test_non_http_scheme_is_rejected():
    client = RetrievalClient(
        _settings(retrieval_allowed_hosts=frozenset({"example.com"}))
    )
    with pytest.raises(PolicyError):
        client.fetch("file:///etc/passwd")


def test_private_ip_literal_is_blocked():
    client = RetrievalClient(
        _settings(retrieval_allowed_hosts=frozenset({"127.0.0.1"}))
    )
    with pytest.raises(PrivateAddressError):
        client.fetch("http://127.0.0.1/secret")


def test_metadata_address_is_blocked():
    client = RetrievalClient(
        _settings(retrieval_allowed_hosts=frozenset({"169.254.169.254"}))
    )
    with pytest.raises(PrivateAddressError):
        client.fetch("http://169.254.169.254/latest/meta-data/")


def test_redirect_to_disallowed_host_is_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "allowed.example":
            return httpx.Response(
                302, headers={"location": "https://evil.example/steal"}, request=request
            )
        return httpx.Response(200, text="ok", request=request)

    client = RetrievalClient(
        _settings(
            retrieval_allowed_hosts=frozenset({"allowed.example"}),
            retrieval_max_redirects=3,
        ),
        transport=FakeTransport(handler),
        resolver=_public_resolver,
    )
    # The redirect target is not on the allowlist, so it is rejected before any
    # connection is attempted.
    with pytest.raises(PolicyError):
        client.fetch("https://allowed.example/start")


def test_payload_size_limit_is_enforced():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100, request=request)

    client = RetrievalClient(
        _settings(
            retrieval_allowed_hosts=frozenset({"allowed.example"}),
            retrieval_max_bytes=10,
        ),
        transport=FakeTransport(handler),
        resolver=_public_resolver,
    )
    with pytest.raises(PayloadTooLargeError):
        client.fetch("https://allowed.example/big")


def test_successful_fetch_returns_document():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b"<html><body><p>hello</p></body></html>",
            headers={"content-type": "text/html; charset=utf-8"},
            request=request,
        )

    client = RetrievalClient(
        _settings(retrieval_allowed_hosts=frozenset({"allowed.example"})),
        transport=FakeTransport(handler),
        resolver=_public_resolver,
    )
    doc = client.fetch("https://allowed.example/page")
    assert doc.status_code == 200
    assert doc.content_hash
    assert doc.charset == "utf-8"
    assert b"hello" in doc.content


def test_robots_parser_respects_disallow():
    robots = """
    User-agent: *
    Disallow: /private/
    Allow: /private/ok
    """
    groups = parse_robots_txt(robots)
    assert robots_allows(groups, "UnmistBot/0.1", "/public/page") is True
    assert robots_allows(groups, "UnmistBot/0.1", "/private/secret") is False
    assert robots_allows(groups, "UnmistBot/0.1", "/private/ok") is True


def test_robots_parser_specific_agent_group():
    robots = """
    User-agent: OtherBot
    Disallow: /

    User-agent: UnmistBot
    Disallow: /admin
    """
    groups = parse_robots_txt(robots)
    assert robots_allows(groups, "UnmistBot/0.1", "/admin/x") is False
    assert robots_allows(groups, "UnmistBot/0.1", "/public") is True
