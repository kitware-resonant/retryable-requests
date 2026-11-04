from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pytest_httpserver import RequestMatcher

from retryable_requests import RetryableSession

if TYPE_CHECKING:
    from pytest_httpserver import HTTPServer


@pytest.mark.parametrize(
    ("base_path", "url", "path"),
    [
        ("/v1/", "items/", "/v1/items/"),
        ("/v1/", "/items/", "/v1/items/"),
        ("/v1", "items/", "/v1/items/"),
        ("/v1/", "../items/", "/items/"),
        ("/v1/", "{origin}/items/", "/items/"),
        ("/v1/", "//{authority}/items/", "/items/"),
        ("/v1/", b"items/", "/v1/items/"),
    ],
    ids=[
        "relative",
        "leading_slash",
        "no_trailing_slash",
        "parent",
        "absolute",
        "scheme_relative",
        "bytes",
    ],
)
def test_session_sends_to_prefixed_url(
    httpserver: HTTPServer, base_path: str, url: str | bytes, path: str
) -> None:
    """A request is sent to its URL with the base URL before it, unless the URL is absolute."""
    httpserver.expect_request(path).respond_with_data()
    if isinstance(url, str):
        authority = f"{httpserver.host}:{httpserver.port}"
        url = url.format(origin=f"http://{authority}", authority=authority)

    with RetryableSession(base_url=httpserver.url_for(base_path)) as session:
        response = session.get(url)

    assert response.ok
    httpserver.assert_request_made(RequestMatcher(path, method="GET"))


@pytest.mark.parametrize("suffix", ["?key=1", "#top"], ids=["query", "fragment"])
def test_session_rejects_base_url_with_query(suffix: str) -> None:
    """A request is refused under a base URL with a query or fragment."""
    with (
        RetryableSession(base_url=f"https://api.example/v1/{suffix}") as session,
        pytest.raises(ValueError, match="base_url"),
    ):
        session.get("items/")
