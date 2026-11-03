# retryable-requests

[![PyPI](https://img.shields.io/pypi/v/retryable-requests)](https://pypi.org/project/retryable-requests/)

_If you fail, don't bail._

A `requests` session with sensible retries and timeouts built in.

A plain `requests` session never retries and never times out. `RetryableSession` does both by
default, so that a brief outage or a stalled connection is handled rather than passed on.

## Installation

```
pip install retryable-requests
```

Python 3.12 or later is required.

## Usage

```python
from retryable_requests import RetryableSession

with RetryableSession(base_url="https://api.example.com/v1/") as session:
    response = session.get("items/")
    response.raise_for_status()
```

`RetryableSession` is a `requests.Session`,
[used like any other](https://docs.python-requests.org/en/latest/user/advanced/#session-objects),
which also:

- retries[^times] a request which:
  - fails to connect, whatever its method
  - loses its connection, if its method is idempotent[^idempotent]
  - fails in TLS, if its method is idempotent
  - times out while waiting for a response, if its method is idempotent
  - gets an HTTP error response[^statuses] which may be transient, if its method is idempotent
- waits between retries with exponential backoff and jitter[^backoff]
  - a `Retry-After` header sets the wait instead[^retry-after]
- gives every request a default timeout[^timeout]
  - a request may pass its own timeout instead
- optionally joins every request's URL to `base_url`, forgiving of slashes
- keeps the error semantics of `requests`:
  - when a failing HTTP response exhausts the retries, it is returned, and can be surfaced by
    `raise_for_status()`
  - a failure to connect or a timeout, once the retries are exhausted, raises an exception

## Options

Every argument to `RetryableSession` is optional and keyword-only.

| Argument         | Type                                                   | Default                  |
| ---------------- | ------------------------------------------------------ | ------------------------ |
| `base_url`       | `str \| None`                                          | `None`                   |
| `timeout`        | `tuple[float \| None, float \| None] \| float \| None` | `(3.05, 30)`             |
| `retry_strategy` | `urllib3.util.Retry`                                   | `DEFAULT_RETRY_STRATEGY` |
| `pool_maxsize`   | `int`                                                  | `10`                     |

### `base_url`

The URL to join each request's URL to, forgiving of slashes: for example,
`https://api.example.com/v1` joins either of `items/` or `/items/` to
`https://api.example.com/v1/items/`.

`base_url` is ignored if a request specifies an absolute URL.
`base_url` may not have a query string or fragment.

### `timeout`

A tuple of the connect and read timeouts in seconds, applied to every request. Either may be
`None`, to disable the timeout. Alternatively, a single value will apply the same timeout to both
connecting and reading.

See the `requests`
[timeouts documentation](https://requests.readthedocs.io/en/stable/user/advanced/#timeouts)
for more details.

### `retry_strategy` (advanced)

A `urllib3.util.Retry` policy, applied to every request.

See the `urllib3`
[documentation](https://urllib3.readthedocs.io/en/stable/reference/urllib3.util.html#urllib3.util.Retry)
for more details.

To change the retry policy, derive one from the default with `Retry.new`, which replaces each
setting it is given rather than adding to it. For example, to also retry `POST` requests which
are known to be safe to repeat:

```python
from retryable_requests import DEFAULT_RETRY_STRATEGY, RetryableSession
from urllib3.util import Retry

retry_strategy = DEFAULT_RETRY_STRATEGY.new(
    allowed_methods=Retry.DEFAULT_ALLOWED_METHODS | {"POST"}
)
session = RetryableSession(retry_strategy=retry_strategy)
```

### `pool_maxsize` (advanced)

The number of connections kept open to each host, which should be at least the number of threads
sharing the session. More threads than that still get connections, but ones opened for a single
request and then closed.

However, note that `requests`
[does not guarantee thread-safety](https://github.com/psf/requests/issues/2766),
particularly when cookies are set.

[^times]: A request is retried up to five times. In the worst case, with the default timeout, a
request which stalls every time fails after over three minutes, and one answered every time with a
long `Retry-After` returns its last response after five minutes.

[^idempotent]: `GET`, `HEAD`, `PUT`, `DELETE`, `OPTIONS` or `TRACE` are idempotent and should be
safe to repeat even if the server has started processing another invocation.

[^statuses]: HTTP statuses 408, 429, 500, 502, 503 or 504 allow that a repeated request may succeed.
A 413 is retried too when a `Retry-After` header marks it as temporary.

[^backoff]: The first retry is immediate; the rest wait 1, 2, 4 and 8 seconds, plus up to half a
second of jitter.

[^retry-after]: A `Retry-After` of more than a minute is shortened to a minute.

[^timeout]: A request times out after 3.05 seconds to connect and 30 seconds between bytes.
