from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest
import requests
from requests.adapters import HTTPAdapter

from retryable_requests import RetryableSession

if TYPE_CHECKING:
    from pytest_httpserver import HTTPServer
    from pytest_mock import MockerFixture

    from handlers import Stall
    from retryable_requests import TimeoutType


def test_session_defaults_timeout(httpserver: HTTPServer, mocker: MockerFixture) -> None:
    """A request is sent with a timeout when neither it nor the session was given one."""
    httpserver.expect_request("/").respond_with_data()
    send = mocker.spy(HTTPAdapter, "send")

    with RetryableSession() as session:
        session.get(httpserver.url_for("/"))

    assert send.call_args.kwargs["timeout"] is not None


def test_session_times_out_request(httpserver: HTTPServer, stall: Stall) -> None:
    """The session's timeout applies to a request."""
    httpserver.expect_request("/").respond_with_handler(stall)

    # POST, so that the timeout is raised rather than retried
    with RetryableSession(timeout=(3.05, 0.1)) as session, pytest.raises(requests.ReadTimeout):
        session.post(httpserver.url_for("/"))


def test_session_timeout_of_none_disables_timeout(
    httpserver: HTTPServer, mocker: MockerFixture, stall: Stall
) -> None:
    """A session's timeout of None disables the timeout, rather than yielding to the default."""
    httpserver.expect_request("/").respond_with_handler(stall)
    threading.Timer(0.5, stall.release.set).start()
    send = mocker.spy(HTTPAdapter, "send")

    with RetryableSession(timeout=None) as session:
        response = session.get(httpserver.url_for("/"))

    assert response.text == "late"
    # The stall is released well within the default timeout, so it alone cannot tell None from it
    assert send.call_args.kwargs["timeout"] is None


@pytest.mark.parametrize("timeout", [7, None], ids=["value", "none"])
def test_request_timeout_overrides_session(
    httpserver: HTTPServer, mocker: MockerFixture, timeout: TimeoutType
) -> None:
    """A request's own timeout, None included, is sent instead of the session's."""
    httpserver.expect_request("/").respond_with_data()
    send = mocker.spy(HTTPAdapter, "send")

    with RetryableSession(timeout=(1, 2)) as session:
        session.get(httpserver.url_for("/"), timeout=timeout)

    assert send.call_args.kwargs["timeout"] == timeout


def test_session_times_out_request_sent_by_hand(httpserver: HTTPServer, stall: Stall) -> None:
    """The session's timeout applies to a request prepared and sent by hand too."""
    httpserver.expect_request("/").respond_with_handler(stall)

    with RetryableSession(timeout=(3.05, 0.1)) as session:
        prepared = session.prepare_request(requests.Request("POST", httpserver.url_for("/")))
        with pytest.raises(requests.ReadTimeout):
            session.send(prepared)
