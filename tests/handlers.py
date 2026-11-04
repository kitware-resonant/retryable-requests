from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from werkzeug.wrappers import Response

if TYPE_CHECKING:
    from pytest_httpserver import HTTPServer
    from werkzeug.wrappers import Request


class Stall:
    """Hold the response until released, so as to outlast a client's read timeout."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = 0
        self.responses: list[Response] = []

    def __call__(self, request: Request) -> Response:
        self.started += 1
        self.release.wait(timeout=5)
        response = Response("late")
        self.responses.append(response)
        return response

    def is_settled(self, httpserver: HTTPServer) -> bool:
        """Whether every call of this handler has returned and been logged by the server."""
        # The server logs a response once its handler returns, so a call which has not returned
        # yet has no response to have been logged
        responses = {id(response) for response in self.responses}
        logged = {id(response) for _, response in httpserver.log}
        return len(self.responses) == self.started and responses <= logged
