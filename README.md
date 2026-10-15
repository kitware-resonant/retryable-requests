# retryable-requests
[![PyPI](https://img.shields.io/pypi/v/retryable-requests)](https://pypi.org/project/retryable-requests/)

Easy to use retryable requests sessions.

## Quickstart

### Common case

``` python
from retryable_requests import RetryableSession

with RetryableSession() as session:
    session.get('https://httpbin.org/get')  # will be retried up to 5 times
```


### Only retry on 429 errors

``` python
from requests.packages.urllib3.util.retry import Retry
from retryable_requests import RetryableSession
