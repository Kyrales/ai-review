import httpx
import pytest
from unittest.mock import AsyncMock, patch

from ai_review.libs.http.transports.retry import NO_RETRY, RetryTransport
from ai_review.libs.logger import get_logger


class CountingTransport(httpx.AsyncBaseTransport):
    """Inner transport that always answers with the same status and counts attempts."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        self.attempts = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.attempts += 1
        return httpx.Response(self.status_code, request=request)


def build_retry_transport(inner: CountingTransport) -> RetryTransport:
    return RetryTransport(
        logger=get_logger("TEST_RETRY_TRANSPORT"),
        transport=inner,
        max_retries=3,
        retry_delay=0,
    )


@pytest.mark.asyncio
async def test_retry_transport_retries_server_errors():
    """Should exhaust max_retries when the server keeps answering 500."""
    inner = CountingTransport(status_code=500)
    transport = build_retry_transport(inner)

    response = await transport.handle_async_request(httpx.Request("POST", "https://gitlab.test/api"))

    assert response.status_code == 500
    assert inner.attempts == 3


@pytest.mark.asyncio
async def test_retry_transport_does_not_retry_when_opted_out():
    """Should make a single attempt for a request marked as non-idempotent."""
    inner = CountingTransport(status_code=500)
    transport = build_retry_transport(inner)
    request = httpx.Request("POST", "https://gitlab.test/api", extensions=NO_RETRY)

    response = await transport.handle_async_request(request)

    assert response.status_code == 500
    assert inner.attempts == 1


@pytest.mark.asyncio
async def test_retry_transport_returns_success_without_retrying():
    """Should return a successful response on the first attempt."""
    inner = CountingTransport(status_code=200)
    transport = build_retry_transport(inner)

    response = await transport.handle_async_request(httpx.Request("GET", "https://gitlab.test/api"))

    assert response.status_code == 200
    assert inner.attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [408, 429])
async def test_retry_transport_retries_transient_client_statuses(status_code: int):
    inner = CountingTransport(status_code=status_code)
    transport = build_retry_transport(inner)

    await transport.handle_async_request(httpx.Request("GET", "https://gitflic.test/api"))

    assert inner.attempts == 3


@pytest.mark.asyncio
async def test_retry_transport_honours_retry_after_seconds():
    class RetryAfterTransport(httpx.AsyncBaseTransport):
        attempts = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.attempts += 1
            status = 429 if self.attempts == 1 else 200
            return httpx.Response(status, headers={"Retry-After": "2"}, request=request)

    inner = RetryAfterTransport()
    transport = build_retry_transport(inner)
    with patch("ai_review.libs.http.transports.retry.asyncio.sleep", new=AsyncMock()) as sleep:
        response = await transport.handle_async_request(httpx.Request("GET", "https://gitflic.test/api"))

    assert response.status_code == 200
    sleep.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
async def test_retry_transport_uses_exponential_backoff():
    inner = CountingTransport(status_code=500)
    transport = RetryTransport(
        logger=get_logger("TEST_RETRY_TRANSPORT"),
        transport=inner,
        max_retries=3,
        retry_delay=0.5,
    )
    with patch("ai_review.libs.http.transports.retry.asyncio.sleep", new=AsyncMock()) as sleep:
        await transport.handle_async_request(httpx.Request("GET", "https://gitflic.test/api"))

    assert [call.args[0] for call in sleep.await_args_list] == [0.5, 1.0]


@pytest.mark.asyncio
async def test_retry_transport_retries_transport_error():
    class FlakyTransport(httpx.AsyncBaseTransport):
        attempts = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.attempts += 1
            if self.attempts == 1:
                raise httpx.ConnectError("temporary", request=request)
            return httpx.Response(200, request=request)

    inner = FlakyTransport()
    transport = build_retry_transport(inner)
    with patch("ai_review.libs.http.transports.retry.asyncio.sleep", new=AsyncMock()):
        response = await transport.handle_async_request(httpx.Request("GET", "https://gitflic.test/api"))

    assert response.status_code == 200
    assert inner.attempts == 2
