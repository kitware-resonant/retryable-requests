from __future__ import annotations

import socket
import threading
import time
from typing import TYPE_CHECKING

import pytest
from pytest_httpserver import HTTPServer

from handlers import Stall

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pytest_mock import MockerFixture


# Overrides pytest-httpserver's fixture of the same name, which its "httpserver" fixture draws
# from, to serve requests concurrently; otherwise a stalled request would hold up its retry.
@pytest.fixture(scope="session")
def make_httpserver() -> Iterator[HTTPServer]:
    with HTTPServer(threaded=True) as server:
        yield server


@pytest.fixture
def httpserver(httpserver: HTTPServer) -> Iterator[HTTPServer]:
    yield httpserver
    # A request which matched no handler is answered with a 500 and only recorded, since the
    # server's thread cannot raise into the test. Left unchecked, that 500 would be retried as a
    # transient status, and a test with a wrong path could pass or fail for the wrong reason.
    httpserver.check()


@pytest.fixture
def stall(httpserver: HTTPServer) -> Iterator[Stall]:
    stall = Stall()
    yield stall
    stall.release.set()
    # The handler may still be running when the test ends; let the server log its responses
    # before the next test clears the log
    deadline = time.monotonic() + 5
    while not stall.is_settled(httpserver):
        assert time.monotonic() < deadline, "The server never logged the handler's responses."
        # Not time.sleep, which the "skipped_sleeps" fixture replaces
        threading.Event().wait(0.01)


@pytest.fixture
def closed_port() -> int:
    """Find a local port which nothing listens on, so that connecting to it is refused."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
    return port


@pytest.fixture(autouse=True)
def skipped_sleeps(mocker: MockerFixture) -> list[float]:
    """Record the seconds the session would sleep between attempts, instead of sleeping."""
    recorded: list[float] = []
    mocker.patch("time.sleep", side_effect=recorded.append)
    return recorded
