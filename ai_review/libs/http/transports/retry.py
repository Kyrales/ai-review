import asyncio
from http import HTTPStatus
from typing import TYPE_CHECKING

from httpx import Request, Response, AsyncBaseTransport, TransportError

if TYPE_CHECKING:
    from loguru import Logger

NO_RETRY_EXTENSION: str = "no_retry"
NO_RETRY: dict[str, bool] = {NO_RETRY_EXTENSION: True}


class RetryTransport(AsyncBaseTransport):
    def __init__(
            self,
            logger: "Logger",
            transport: AsyncBaseTransport,
            max_retries: int = 5,
            retry_delay: float = 0.5,
            retry_status_codes: tuple[HTTPStatus, ...] = (
                    HTTPStatus.REQUEST_TIMEOUT,
                    HTTPStatus.TOO_MANY_REQUESTS,
                    HTTPStatus.BAD_GATEWAY,
                    HTTPStatus.GATEWAY_TIMEOUT,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    HTTPStatus.INTERNAL_SERVER_ERROR,
            )
    ):
        self.logger = logger
        self.transport = transport
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.retry_status_codes = retry_status_codes

    async def handle_async_request(self, request: Request) -> Response:
        if request.extensions.get(NO_RETRY_EXTENSION):
            return await self.transport.handle_async_request(request)

        last_response: Response | None = None
        for attempt in range(self.max_retries):
            try:
                last_response = await self.transport.handle_async_request(request)
            except TransportError as error:
                if attempt + 1 >= self.max_retries:
                    raise
                delay = self.retry_delay * (2 ** attempt)
                self.logger.warning(
                    f"Attempt {attempt + 1}/{self.max_retries} failed with "
                    f"{type(error).__name__} for {request.method} {request.url}. "
                    f"Retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
                continue
            if last_response.status_code not in self.retry_status_codes:
                return last_response

            if attempt + 1 >= self.max_retries:
                break

            retry_after = last_response.headers.get("Retry-After", "")
            try:
                delay = max(0.0, float(retry_after)) if retry_after else self.retry_delay * (2 ** attempt)
            except ValueError:
                delay = self.retry_delay * (2 ** attempt)

            self.logger.warning(
                f"Attempt {attempt + 1}/{self.max_retries} failed "
                f"with status={last_response.status_code} for {request.method} {request.url}. "
                f"Retrying in {delay:.1f}s..."
            )

            await last_response.aclose()
            await asyncio.sleep(delay)

        self.logger.error(
            f"All {self.max_retries} attempts failed for "
            f"{request.method} {request.url} (last status={last_response.status_code})"
        )

        return last_response
