from __future__ import annotations

import ssl
from typing import TYPE_CHECKING

import pytest
from pytest_httpserver import HTTPServer, RequestMatcher
import requests
import trustme
from urllib3.connectionpool import HTTPConnectionPool
from urllib3.util import Retry

from retryable_requests import DEFAULT_RETRY_STRATEGY, RetryableSession

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

    from handlers import Stall


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
def test_session_retries_transient_status(httpserver: HTTPServer, status: int) -> None:
    """A response with a transient status is retried, and the retry's response is returned."""
    httpserver.expect_oneshot_request("/").respond_with_data(status=status)
    httpserver.expect_request("/").respond_with_data()

    with RetryableSession() as session:
        response = session.get(httpserver.url_for("/"))

    assert response.ok
    httpserver.assert_request_made(RequestMatcher("/"), count=2)


@pytest.mark.parametrize("status", [404, 413, 501])
def test_session_returns_other_status(httpserver: HTTPServer, status: int) -> None:
    """A response with any other status is returned as it is, without a retry."""
    httpserver.expect_oneshot_request("/").respond_with_data(status=status)
    httpserver.expect_request("/").respond_with_data()

    with RetryableSession() as session:
        response = session.get(httpserver.url_for("/"))

    assert response.status_code == status
    httpserver.assert_request_made(RequestMatcher("/"), count=1)


def test_session_retries_413_with_retry_after(
    httpserver: HTTPServer, skipped_sleeps: list[float]
) -> None:
    """A 413 is retried when a Retry-After header marks it as temporary."""
    httpserver.expect_oneshot_request("/").respond_with_data(
        status=413, headers={"Retry-After": "2"}
    )
    httpserver.expect_request("/").respond_with_data()

    with RetryableSession() as session:
        response = session.get(httpserver.url_for("/"))

    assert response.ok
    assert skipped_sleeps == [2]
    httpserver.assert_request_made(RequestMatcher("/"), count=2)


def test_session_does_not_retry_other_method(httpserver: HTTPServer) -> None:
    """A method which is not idempotent is not retried; its failed response is returned."""
    httpserver.expect_oneshot_request("/").respond_with_data(status=503)
    httpserver.expect_request("/").respond_with_data()

    with RetryableSession() as session:
        response = session.post(httpserver.url_for("/"))

    assert response.status_code == 503
    httpserver.assert_request_made(RequestMatcher("/", method="POST"), count=1)


def test_session_retries_method_opted_in(httpserver: HTTPServer) -> None:
    """A method is retried once a derived retry strategy allows it."""
    httpserver.expect_oneshot_request("/").respond_with_data(status=503)
    httpserver.expect_request("/").respond_with_data()
    retry_strategy = DEFAULT_RETRY_STRATEGY.new(
        allowed_methods=Retry.DEFAULT_ALLOWED_METHODS | {"POST"}
    )

    with RetryableSession(retry_strategy=retry_strategy) as session:
        response = session.post(httpserver.url_for("/"), data=b"once")

    assert response.ok
    httpserver.assert_request_made(RequestMatcher("/", method="POST"), count=2)


def test_session_retries_timed_out_read(httpserver: HTTPServer, stall: Stall) -> None:
    """A read which times out is retried."""
    httpserver.expect_oneshot_request("/").respond_with_handler(stall)
    httpserver.expect_request("/").respond_with_data("fine")

    with RetryableSession(timeout=(3.05, 0.1)) as session:
        response = session.get(httpserver.url_for("/"))

    # Only a retry could have reached the second handler
    assert response.text == "fine"


def test_session_retries_failure_to_connect_for_any_method(
    mocker: MockerFixture, closed_port: int
) -> None:
    """A refused connection is retried for any method."""
    urlopen = mocker.spy(HTTPConnectionPool, "urlopen")

    with RetryableSession() as session, pytest.raises(requests.ConnectionError):
        session.post(f"http://127.0.0.1:{closed_port}/")

    # The request and its five retries
    assert urlopen.call_count == 6


@pytest.mark.parametrize(
    ("method", "expected_attempts"), [("GET", 6), ("POST", 1)], ids=["idempotent", "other"]
)
def test_session_retries_tls_failure_for_idempotent_method(
    mocker: MockerFixture, method: str, expected_attempts: int
) -> None:
    """A failure in TLS is retried like a lost connection, for an idempotent method only."""
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    trustme.CA().issue_cert("127.0.0.1").configure_cert(context)
    urlopen = mocker.spy(HTTPConnectionPool, "urlopen")

    # Bind to an address rather than "localhost", as pytest-httpserver advises for a TLS failure:
    # given the name, a client may try the other address family after the failure, and fail
    # differently.
    with (
        HTTPServer(host="127.0.0.1", ssl_context=context) as server,
        RetryableSession() as session,
        pytest.raises(requests.exceptions.SSLError),
    ):
        session.request(method, server.url_for("/"))

    assert urlopen.call_count == expected_attempts


def test_session_backs_off_between_retries(
    httpserver: HTTPServer, mocker: MockerFixture, skipped_sleeps: list[float]
) -> None:
    """After an immediate first retry, the waits double from a second, plus jitter."""
    httpserver.expect_request("/").respond_with_data(status=503)
    # Hold the jitter at half its range, so the waits are exact
    mocker.patch("random.random", return_value=0.5)

    with RetryableSession() as session:
        session.get(httpserver.url_for("/"))

    assert skipped_sleeps == [1.25, 2.25, 4.25, 8.25]


def test_session_clamps_retry_after(httpserver: HTTPServer, skipped_sleeps: list[float]) -> None:
    """A Retry-After header sets the wait before the retry, up to a minute."""
    httpserver.expect_oneshot_request("/").respond_with_data(
        status=503, headers={"Retry-After": "3600"}
    )
    httpserver.expect_request("/").respond_with_data()

    with RetryableSession() as session:
        response = session.get(httpserver.url_for("/"))

    assert response.ok
    assert skipped_sleeps == [60]


def test_session_returns_last_response_when_retries_run_out(httpserver: HTTPServer) -> None:
    """When every retry fails, the last response is returned rather than an exception raised."""
    httpserver.expect_request("/").respond_with_data("down", status=503)

    with RetryableSession() as session:
        response = session.get(httpserver.url_for("/"))

    assert response.status_code == 503
    assert response.text == "down"
    # The request and its five retries
    httpserver.assert_request_made(RequestMatcher("/"), count=6)
