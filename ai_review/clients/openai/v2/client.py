import asyncio
import json

from httpx import Response, AsyncClient, AsyncHTTPTransport
from pydantic import ValidationError

from ai_review.clients.openai.v2.schema import (
    OpenAIResponsesRequestSchema,
    OpenAIResponsesResponseSchema
)
from ai_review.clients.openai.v2.types import OpenAIV2HTTPClientProtocol
from ai_review.libs.http.client import HTTPClient
from ai_review.libs.http.event_hooks.logger import LoggerEventHook
from ai_review.libs.http.handlers import HTTPClientError, handle_http_error
from ai_review.libs.http.transports.retry import RetryTransport
from ai_review.libs.config.llm.openai import OpenAIHTTPClientConfig


class OpenAIV2HTTPClientError(HTTPClientError):
    pass


class OpenAIV2ProtocolError(ValueError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def parse_responses_response(response: Response) -> OpenAIResponsesResponseSchema:
    content_type = response.headers.get("content-type", "").lower()
    if "text/event-stream" not in content_type and not response.text.lstrip().startswith(("event:", "data:", ":")):
        return OpenAIResponsesResponseSchema.model_validate_json(response.text)

    if len(response.content) > 20 * 1024 * 1024:
        raise OpenAIV2ProtocolError("SSE response exceeds 20 MiB")

    completed: dict | None = None
    event_name: str | None = None
    data_lines: list[str] = []
    text_deltas: list[str] = []
    done_text = ""
    done_seen = False
    events = 0

    def consume() -> None:
        nonlocal completed, event_name, data_lines, events, done_text, done_seen
        if not data_lines:
            event_name = None
            return
        events += 1
        if events > 100_000:
            raise OpenAIV2ProtocolError("SSE response has too many events")
        data = "\n".join(data_lines)
        data_lines = []
        if data == "[DONE]":
            event_name = None
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as error:
            raise OpenAIV2ProtocolError("SSE event contains invalid JSON") from error
        payload_type = payload.get("type")
        if event_name and event_name != "codex.keepalive" and payload_type and event_name != payload_type:
            raise OpenAIV2ProtocolError(
                "SSE event name does not match payload type",
                retryable=True,
            )
        if payload_type in {"response.failed", "response.incomplete"}:
            retryable = payload_type == "response.failed"
            raise OpenAIV2ProtocolError(
                f"SSE terminated with {payload_type}", retryable=retryable,
            )
        if payload_type == "response.output_text.delta":
            delta = payload.get("delta")
            if not isinstance(delta, str):
                raise OpenAIV2ProtocolError("response.output_text.delta has no text")
            text_deltas.append(delta)
        if payload_type == "response.output_text.done":
            text = payload.get("text")
            if not isinstance(text, str):
                raise OpenAIV2ProtocolError("response.output_text.done has no text")
            if done_seen and done_text != text:
                raise OpenAIV2ProtocolError(
                    "SSE contains conflicting response.output_text.done events",
                    retryable=True,
                )
            done_text = text
            done_seen = True
        if payload_type == "response.completed":
            if completed is not None:
                raise OpenAIV2ProtocolError(
                    "SSE contains multiple response.completed events",
                    retryable=True,
                )
            completed = payload.get("response")
            if not isinstance(completed, dict):
                raise OpenAIV2ProtocolError("response.completed has no response object")
        event_name = None

    for line in response.text.splitlines():
        if not line:
            consume()
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    consume()

    if completed is None:
        raise OpenAIV2ProtocolError(
            "SSE has no response.completed event",
            retryable=True,
        )
    delta_text = "".join(text_deltas)
    if done_seen and text_deltas and done_text != delta_text:
        raise OpenAIV2ProtocolError("SSE final text does not match streamed deltas", retryable=True)
    try:
        parsed = OpenAIResponsesResponseSchema.model_validate(completed)
        completed_parts: list[str] = []
        completed_text_seen = False
        for block in parsed.output:
            if block.type != "message" or not block.content:
                continue
            for content in block.content:
                if content.type == "output_text" and content.text is not None:
                    completed_text_seen = True
                    completed_parts.append(content.text)
        completed_text = "".join(completed_parts)
        stream_seen = done_seen or bool(text_deltas)
        stream_text = done_text if done_seen else delta_text
        if stream_seen and completed_text_seen and completed_text != stream_text:
            raise OpenAIV2ProtocolError(
                "SSE text does not match completed response",
                retryable=True,
            )
        if not completed_text_seen and stream_text:
            completed = {
                **completed,
                "output": [{
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": stream_text}],
                }],
            }
            parsed = OpenAIResponsesResponseSchema.model_validate(completed)
        return parsed
    except ValidationError as error:
        raise OpenAIV2ProtocolError("response.completed payload is invalid") from error


class OpenAIV2HTTPClient(HTTPClient, OpenAIV2HTTPClientProtocol):
    @handle_http_error(client='OpenAIV2HTTPClient', exception=OpenAIV2HTTPClientError)
    async def chat_api(self, request: OpenAIResponsesRequestSchema) -> Response:
        return await self.post("responses", json=request.model_dump(exclude_none=True))

    async def chat(self, request: OpenAIResponsesRequestSchema) -> OpenAIResponsesResponseSchema:
        for attempt in range(3):
            response = await self.chat_api(request)
            try:
                return parse_responses_response(response)
            except OpenAIV2ProtocolError as error:
                if not error.retryable or attempt == 2:
                    raise
                await asyncio.sleep(0.5 * (2 ** attempt))
        raise AssertionError("unreachable")


def get_openai_v2_http_client(
    config: OpenAIHTTPClientConfig | None = None,
    timeout: float | None = None,
) -> OpenAIV2HTTPClient:
    if config is None:
        from ai_review.config import settings
        from ai_review.libs.logger import get_logger

        http_config = settings.llm.http_client
        logger = get_logger("OPENAI_V2_HTTP_CLIENT")
    else:
        from loguru import logger as root_logger

        http_config = config
        logger = root_logger.bind(logger_name="OPENAI_V2_HTTP_CLIENT")
    logger_event_hook = LoggerEventHook(logger=logger)
    retry_transport = RetryTransport(
        logger=logger,
        transport=AsyncHTTPTransport(
            proxy=http_config.proxy_url_value,
            verify=http_config.verify
        )
    )

    client = AsyncClient(
        verify=http_config.verify,
        timeout=timeout if timeout is not None else http_config.timeout,
        headers={"Authorization": f"Bearer {http_config.api_token_value}"},
        base_url=http_config.api_url_value,
        transport=retry_transport,
        event_hooks={
            'request': [logger_event_hook.request],
            'response': [logger_event_hook.response]
        }
    )

    return OpenAIV2HTTPClient(client=client)
