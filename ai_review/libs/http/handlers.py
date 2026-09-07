from functools import wraps
from typing import Callable, Coroutine, Any

from httpx import Response, HTTPStatusError

APIFunc = Callable[..., Coroutine[Any, Any, Response]]


class HTTPClientError(Exception):
    def __init__(
            self,
            client: str,
            details: str,
            status_code: int,
            request_id: str | None = None,
    ):
        self.details = f'[{client}]: {details}'
        self.status_code = status_code
        self.request_id = request_id

        super().__init__(f"[{client}] {status_code}: {details}")


def handle_http_error(client: str, exception: type[HTTPClientError]):
    def safe_request_id(response: Response) -> str | None:
        for name in ("x-request-id", "request-id"):
            value = response.headers.get(name)
            if value and len(value) <= 128 and all(32 <= ord(char) <= 126 for char in value):
                return value
        return None

    def wrapper(func: APIFunc):
        @wraps(func)
        async def inner(*args, **kwargs):
            response = await func(*args, **kwargs)

            try:
                return response.raise_for_status()
            except HTTPStatusError as error:
                raise exception(
                    client=client,
                    details=f'{client} returned HTTP error',
                    status_code=error.response.status_code,
                    request_id=safe_request_id(error.response),
                ) from error

        return inner

    return wrapper
