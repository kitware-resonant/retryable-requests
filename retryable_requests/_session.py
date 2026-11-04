from __future__ import annotations

from copy import copy
import sys
from typing import TYPE_CHECKING, Any, override
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from requests.adapters import DEFAULT_POOLSIZE, HTTPAdapter
from urllib3.exceptions import ProxyError, SSLError
from urllib3.util import Retry

if TYPE_CHECKING:
    from http.cookiejar import CookieJar

    from requests import PreparedRequest, Request, Response, _types
    from requests.cookies import RequestsCookieJar

type TimeoutType = tuple[float | None, float | None] | float | None

if sys.version_info < (3, 15):
    from typing_extensions import Sentinel as sentinel  # noqa: N813

SESSION_TIMEOUT = sentinel("SESSION_TIMEOUT")


class SSLAwareRetry(Retry):
    """A ``Retry`` which treats a failure in TLS as a read error, so it's retried if idempotent."""

    # By default, the Retry class allows an SSLError to fall through to be classified as "other",
    # which doesn't examine the HTTP method to determine if a retry would be idempotent. Since
    # SSLError could occur even while receiving a response (not only during initial TLS handshake),
    # it's not safe to retry "other" errors generally.
    #
    # This reclassifies an SSLError as "read", which will allow the HTTP method to determine
    # retry-ability, and which is probably more correct generally.
    # See the discussion in: https://github.com/urllib3/urllib3/pull/1241
    @override
    def _is_read_error(self, err: Exception) -> bool:
        # Like "Retry._is_connection_error", ProxyError must be unwrapped.
        if isinstance(err, ProxyError):
            err = err.original_error
        return isinstance(err, SSLError) or super()._is_read_error(err)


# Other libraries' defaults are cited below as of October 2026.
DEFAULT_RETRY_STRATEGY = SSLAwareRetry(
    # Five retries sleep about fifteen seconds in all, with the backoffs below, on top of the
    # attempts' own time: long enough for a brief outage or a restart to pass. The count is
    # arbitrary; pip retries five times too, botocore four in its legacy mode and two in its
    # standard mode, and ky and got twice, for callers waiting on each request.
    total=5,
    # urllib3 counts an error it can class as neither a failure to connect nor a read error against
    # this, and retries it for any method, with no check of "allowed_methods". Since idempotency
    # cannot be guaranteed, don't retry it.
    other=0,
    # The statuses whose cause may pass, so that a repeat may succeed:
    # * 408 Request Timeout: the server stopped waiting for the request, and the client "MAY
    #   repeat that request" (RFC 9110 §15.5.9)
    # * 429 Too Many Requests: the client is being rate limited, perhaps with a Retry-After to say
    #   for how long (RFC 6585 §4)
    # * 500 Internal Server Error: "an unexpected condition" (RFC 9110 §15.6.1), the catch-all for
    #   a fault, which may not recur
    # * 502 Bad Gateway: a gateway "received an invalid response" from upstream (RFC 9110 §15.6.3)
    # * 503 Service Unavailable: "temporary overload or scheduled maintenance, which will likely be
    #   alleviated after some delay" (RFC 9110 §15.6.4)
    # * 504 Gateway Timeout: a gateway "did not receive a timely response" from upstream
    #   (RFC 9110 §15.6.5)
    # Not:
    # * 501 Not Implemented and 505 HTTP Version Not Supported, RFC 9110's other 5xx, which are
    #   the server declining the request as made
    # * any other 4xx, where "the client seems to have erred" (RFC 9110 §15.5)
    # Unlisted, yet still retried when it carries a Retry-After, which urllib3 does on its own for
    # the statuses in "Retry.RETRY_AFTER_STATUS_CODES":
    # * 413 Content Too Large, which only a Retry-After marks temporary (RFC 9110 §15.5.14)
    # Use frozenset, since "new" passes it on by reference.
    status_forcelist=frozenset({408, 429, 500, 502, 503, 504}),
    # urllib3 retries at once the first time, then after factor * 2^(n - 1): 1, 2, 4 and 8
    # seconds, plus jitter. An immediate retry suits a common failure, a connection the server
    # closed as idle just as it was reused, which a new connection cures (RFC 9110 §9.2.2). The
    # factor is arbitrary: ky starts at 0.3 s and got at 1 s.
    backoff_factor=0.5,
    # Not reached with five retries; this caps a policy derived from this one with more. Thirty
    # seconds is arbitrary: botocore's standard mode caps at 20 and urllib3 at 120.
    backoff_max=30,
    # urllib3 adds a uniform random amount in [0, backoff_jitter) to each backoff, after the
    # exponential term and before the "backoff_max" cap, so the spread is fixed rather than in
    # proportion to the backoff. The immediate first retry gets none, nor does a sleep taken from
    # a Retry-After. Half a second varies the backoffs above by up to half the smallest.
    backoff_jitter=0.5,
    # A Retry-After says how long the client "ought to wait", not must (RFC 9110 §10.2.3). urllib3's
    # default of six hours would stall the caller for a long throttle, such as a daily quota's
    # reset. This maximum potentially shortens each sleep; it does not stop the retrying, as urllib3
    # has no way to give up on a Retry-After longer than it (which would actually be the ideal
    # behavior). So a server which keeps answering with a long Retry-After costs this maximum per
    # retry, five minutes in all here, before its last response is returned. A minute is arbitrary:
    # the AWS SDK for JavaScript caps a Retry-After at just over its own backoff, the OpenAI Python
    # SDK ignores one over two minutes, and its Node SDK one over a minute.
    retry_after_max=60,
    # Return the last failing response instead of raising a "MaxRetryError", which requests would
    # surface as a "RetryError" without the response, losing the body's explanation.
    raise_on_status=False,
)

# The connect timeout gives a lost SYN's first retransmission two seconds to be answered: TCP
# retransmits an unanswered SYN after one second (RFC 6298, rule (2.1)), then after doubling waits
# (rule (5.5)), or on Linux since 6.5 at one-second intervals a few times first
# ("tcp_syn_linear_timeouts"). The margin over three follows requests' documentation, which advises
# "slightly larger than a multiple of 3", the older initial wait which an implementation may still
# use (rule (2.1) again). The read timeout is the longest wait between bytes, not a bound on the
# whole request, so it fires only for a stalled connection; thirty seconds is arbitrary, between
# ky's ten per attempt for the response to begin and botocore's sixty between bytes.
DEFAULT_TIMEOUT: TimeoutType = (3.05, 30)


def _join_base_url(base_url: str, url: str) -> str:
    """Put a base URL before a URL which is not absolute."""
    # A base URL names a root for the URLs beneath it, so one starting with a slash goes there
    # too, rather than resetting to the host's root as RFC 3986 (5.2.2) would have it; httpx and
    # niquests join their base URLs the same way. Anything else resolves as usual: ".." climbs.
    base = urlsplit(base_url)
    # Checked here rather than in "__init__", since "base_url" is a plain attribute which may be
    # set at any time
    if base.query or base.fragment:
        raise ValueError(
            f"base_url must have no query or fragment, which a joined URL would drop: {base_url!r}"
        )
    if not base.path.endswith("/"):
        base = base._replace(path=base.path + "/")
    reference = urlsplit(url)
    if not (reference.scheme or reference.netloc):
        url = url.lstrip("/")
    # urljoin passes an absolute URL through, and gives one which names a host the base's scheme
    return urljoin(urlunsplit(base), url)


class RetryableSession(requests.Session):
    """A ``requests`` session with retries, a default timeout and a base URL built in.

    A timeout given with a request, ``None`` included, takes precedence over the session's. A URL
    given with a request is prefixed with ``base_url`` unless it is absolute.
    """

    # requests pickles a session by these
    __attrs__ = [*requests.Session.__attrs__, "base_url", "timeout"]

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: TimeoutType = DEFAULT_TIMEOUT,
        retry_strategy: Retry = DEFAULT_RETRY_STRATEGY,
        pool_maxsize: int = DEFAULT_POOLSIZE,
    ) -> None:
        super().__init__()
        self.base_url = base_url
        self.timeout = timeout
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_maxsize=pool_maxsize,
        )
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    @override
    def prepare_request(self, request: Request) -> PreparedRequest:
        url = request.url
        if self.base_url is not None and url is not None:
            # Upstream doesn't mutate the "request" parameter
            request = copy(request)
            request.url = _join_base_url(
                self.base_url,
                # "PreparedRequest.prepare_url" tolerates looser types, so we should too
                url.decode("utf-8") if isinstance(url, bytes) else str(url),
            )
        return super().prepare_request(request)

    # Upstream's parameters are repeated, rather than taken as "*args" and "**kwargs", so that a
    # caller's keywords are still type checked and a timeout passed positionally lands here.
    # "test_request_signature_matches_upstream" fails if upstream's parameters change.
    @override
    def request(
        self,
        method: str,
        url: _types.UriType,
        params: _types.ParamsType = None,
        data: _types.DataType = None,
        headers: _types.HeadersType = None,
        cookies: RequestsCookieJar | CookieJar | dict[str, str] | None = None,
        files: _types.FilesType = None,
        auth: _types.AuthType = None,
        # "timeout" is the only actually overridden parameter
        timeout: TimeoutType | SESSION_TIMEOUT = SESSION_TIMEOUT,
        allow_redirects: bool = True,
        proxies: dict[str, str] | None = None,
        hooks: _types.HooksInputType | None = None,
        stream: bool | None = None,
        verify: _types.VerifyType | None = None,
        cert: _types.CertType = None,
        json: _types.JsonType = None,
    ) -> Response:
        if timeout is SESSION_TIMEOUT:
            timeout = self.timeout
        return super().request(
            method,
            url,
            params=params,
            data=data,
            headers=headers,
            cookies=cookies,
            files=files,
            auth=auth,
            timeout=timeout,
            allow_redirects=allow_redirects,
            proxies=proxies,
            hooks=hooks,
            stream=stream,
            verify=verify,
            cert=cert,
            json=json,
        )

    # Overriding "send" isn't redundant with "request", as a request prepared and sent by hand
    # passes only through here.
    @override
    def send(
        self,
        request: PreparedRequest,
        *,
        timeout: TimeoutType | SESSION_TIMEOUT = SESSION_TIMEOUT,
        **kwargs: Any,
    ) -> Response:
        if timeout is SESSION_TIMEOUT:
            timeout = self.timeout
        return super().send(request, timeout=timeout, **kwargs)
