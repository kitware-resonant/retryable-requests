from __future__ import annotations

import inspect
import pickle

import requests
from requests.adapters import HTTPAdapter

from retryable_requests import DEFAULT_RETRY_STRATEGY, RetryableSession


def test_session_pickles() -> None:
    """A session survives pickling with its base URL, timeout and retry strategy."""
    retry_strategy = DEFAULT_RETRY_STRATEGY.new(total=2)
    session = RetryableSession(
        base_url="https://api.example/v1/", retry_strategy=retry_strategy, timeout=(1, 2)
    )

    copied = pickle.loads(pickle.dumps(session))

    assert copied.base_url == "https://api.example/v1/"
    assert copied.timeout == (1, 2)
    adapter = copied.get_adapter("https://example")
    assert isinstance(adapter, HTTPAdapter)
    assert adapter.max_retries.total == 2


def test_request_signature_matches_upstream() -> None:
    """The override of "request" takes upstream's parameters, except for the timeout's default."""
    ours = inspect.signature(RetryableSession.request).parameters
    theirs = inspect.signature(requests.Session.request).parameters

    assert list(ours) == list(theirs)
    for name, their_parameter in theirs.items():
        our_parameter = ours[name]
        assert our_parameter.kind == their_parameter.kind, name
        if name != "timeout":
            assert our_parameter.default == their_parameter.default, name


def test_send_signature_matches_upstream() -> None:
    """The override of "send" takes upstream's parameters, with only a timeout added."""
    ours = inspect.signature(RetryableSession.send).parameters
    theirs = inspect.signature(requests.Session.send).parameters

    assert [name for name in ours if name != "timeout"] == list(theirs)
    for name, their_parameter in theirs.items():
        assert ours[name].kind == their_parameter.kind, name
        assert ours[name].default == their_parameter.default, name
