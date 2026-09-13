import json

import pytest
from httpx import AsyncClient, MockTransport, Request, Response
from pydantic import HttpUrl, SecretStr

from ai_review.clients.openai.v2.client import (
    get_openai_v2_http_client,
    OpenAIV2HTTPClient,
    OpenAIV2ProtocolError,
    parse_responses_response,
)
from ai_review.clients.openai.v2.schema import OpenAIResponsesRequestSchema
from ai_review.libs.config.llm.openai import OpenAIHTTPClientConfig


@pytest.mark.usefixtures('openai_v2_http_client_config')
def test_get_openai_v2_http_client_builds_ok():
    openai_http_client = get_openai_v2_http_client()

    assert isinstance(openai_http_client, OpenAIV2HTTPClient)
    assert isinstance(openai_http_client.client, AsyncClient)


def test_get_openai_v2_http_client_accepts_explicit_config_and_timeout():
    config = OpenAIHTTPClientConfig(
        api_url=HttpUrl("https://codex.example/backend-api/codex"),
        api_token=SecretStr("explicit-token"),
        verify=False,
        timeout=12,
    )

    openai_http_client = get_openai_v2_http_client(config=config, timeout=2700.0)

    assert str(openai_http_client.client.base_url) == (
        "https://codex.example/backend-api/codex/"
    )
    assert openai_http_client.client.headers["Authorization"] == (
        "Bearer explicit-token"
    )
    assert openai_http_client.client.timeout.read == 2700.0


def _response_payload(text: str = "Замечание") -> dict:
    return {
        "usage": {"total_tokens": 3, "input_tokens": 2, "output_tokens": 1},
        "output": [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        }],
    }


def test_parse_plain_json_response():
    response = Response(200, json=_response_payload("JSON result"))

    parsed = parse_responses_response(response)

    assert parsed.first_text == "JSON result"


@pytest.mark.parametrize("terminal_type", ["response.failed", "response.incomplete"])
def test_parse_rejects_unsuccessful_sse_terminal_event(terminal_type: str):
    payload = {"type": terminal_type, "response": {"status": "failed"}}
    response = Response(
        200,
        text=f"event: {terminal_type}\ndata: {json.dumps(payload)}\n\n",
        headers={"content-type": "text/event-stream"},
    )

    with pytest.raises(OpenAIV2ProtocolError, match=terminal_type):
        parse_responses_response(response)


def test_parse_rejects_sse_larger_than_20_mib():
    response = Response(
        200,
        content=b":" + b"x" * (20 * 1024 * 1024),
        headers={"content-type": "text/event-stream"},
    )

    with pytest.raises(OpenAIV2ProtocolError, match="20 MiB"):
        parse_responses_response(response)


def test_parse_rejects_more_than_100000_sse_events():
    response = Response(
        200,
        text=("data: {}\n\n" * 100_001),
        headers={"content-type": "text/event-stream"},
    )

    with pytest.raises(OpenAIV2ProtocolError, match="too many events"):
        parse_responses_response(response)


def test_parse_rejects_multiple_completed_events():
    completed = {"type": "response.completed", "response": _response_payload()}
    response = Response(
        200,
        text="".join(
            f"event: response.completed\ndata: {json.dumps(completed)}\n\n"
            for _ in range(2)
        ),
        headers={"content-type": "text/event-stream"},
    )

    with pytest.raises(OpenAIV2ProtocolError, match="multiple"):
        parse_responses_response(response)


def test_parse_rejects_stream_text_that_differs_from_completed_response():
    completed = {
        "type": "response.completed",
        "response": _response_payload("completed text"),
    }
    body = (
        "event: response.output_text.delta\n"
        "data: {\"type\":\"response.output_text.delta\",\"delta\":\"stream text\"}\n\n"
        "event: response.output_text.done\n"
        "data: {\"type\":\"response.output_text.done\",\"text\":\"stream text\"}\n\n"
        "event: response.completed\n"
        f"data: {json.dumps(completed)}\n\n"
    )
    response = Response(
        200,
        text=body,
        headers={"content-type": "text/event-stream"},
    )

    with pytest.raises(OpenAIV2ProtocolError, match="completed response"):
        parse_responses_response(response)


def test_parse_compares_stream_with_raw_completed_text_before_stripping():
    completed = {
        "type": "response.completed",
        "response": _response_payload("completed text\n"),
    }
    body = (
        "event: response.output_text.delta\n"
        "data: {\"type\":\"response.output_text.delta\",\"delta\":\"completed text\\n\"}\n\n"
        "event: response.output_text.done\n"
        "data: {\"type\":\"response.output_text.done\",\"text\":\"completed text\\n\"}\n\n"
        "event: response.completed\n"
        f"data: {json.dumps(completed)}\n\n"
    )
    response = Response(
        200,
        text=body,
        headers={"content-type": "text/event-stream"},
    )

    parsed = parse_responses_response(response)

    assert parsed.first_text == "completed text"


def test_parse_treats_empty_done_text_as_present():
    completed = {
        "type": "response.completed",
        "response": _response_payload("completed text"),
    }
    body = (
        "event: response.output_text.done\n"
        "data: {\"type\":\"response.output_text.done\",\"text\":\"\"}\n\n"
        "event: response.completed\n"
        f"data: {json.dumps(completed)}\n\n"
    )
    response = Response(
        200,
        text=body,
        headers={"content-type": "text/event-stream"},
    )

    with pytest.raises(OpenAIV2ProtocolError, match="completed response"):
        parse_responses_response(response)


def test_parse_treats_explicit_empty_completed_text_as_present():
    completed = {
        "type": "response.completed",
        "response": _response_payload(""),
    }
    body = (
        "event: response.output_text.delta\n"
        "data: {\"type\":\"response.output_text.delta\",\"delta\":\"stream text\"}\n\n"
        "event: response.output_text.done\n"
        "data: {\"type\":\"response.output_text.done\",\"text\":\"stream text\"}\n\n"
        "event: response.completed\n"
        f"data: {json.dumps(completed)}\n\n"
    )
    response = Response(
        200,
        text=body,
        headers={"content-type": "text/event-stream"},
    )

    with pytest.raises(OpenAIV2ProtocolError, match="completed response"):
        parse_responses_response(response)


@pytest.mark.asyncio
async def test_chat_api_posts_to_responses_below_the_configured_base_path():
    captured = None

    async def handler(request: Request) -> Response:
        nonlocal captured
        captured = request
        return Response(200, json=_response_payload())

    client = OpenAIV2HTTPClient(
        AsyncClient(
            base_url="https://codex.example/backend-api/codex/",
            headers={"Authorization": "Bearer explicit-token"},
            transport=MockTransport(handler),
        )
    )
    request = OpenAIResponsesRequestSchema(model="test", input=[])

    await client.chat_api(request)

    assert captured is not None
    assert str(captured.url) == (
        "https://codex.example/backend-api/codex/responses"
    )
    assert captured.headers["Authorization"] == "Bearer explicit-token"
    assert json.loads(captured.content) == request.model_dump(exclude_none=True)


@pytest.mark.asyncio
async def test_chat_parses_codex_sse_completed_response():
    completed = {"type": "response.completed", "response": _response_payload()}
    body = (
        ": keepalive\r\n\r\n"
        "event: codex.keepalive\r\n\r\n"
        "event: response.completed\r\n"
        f"data: {json.dumps(completed)}\r\n\r\n"
        "data: [DONE]\r\n\r\n"
    )

    async def handler(_: Request) -> Response:
        return Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = OpenAIV2HTTPClient(AsyncClient(
        base_url="https://codex.example/backend-api/codex",
        transport=MockTransport(handler),
    ))
    response = await client.chat(OpenAIResponsesRequestSchema(model="test", input=[]))

    assert response.first_text == "Замечание"


@pytest.mark.asyncio
async def test_chat_rejects_sse_without_completed_response():
    body = "event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"x\"}\n\ndata: [DONE]\n\n"

    async def handler(_: Request) -> Response:
        return Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = OpenAIV2HTTPClient(AsyncClient(
        base_url="https://codex.example/backend-api/codex",
        transport=MockTransport(handler),
    ))

    with pytest.raises(OpenAIV2ProtocolError, match="response.completed"):
        await client.chat(OpenAIResponsesRequestSchema(model="test", input=[]))


@pytest.mark.asyncio
async def test_chat_retries_sse_without_completed_response(monkeypatch):
    attempts = 0
    completed = {"type": "response.completed", "response": _response_payload()}

    async def handler(_: Request) -> Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            body = "event: response.output_text.delta\ndata: {\"type\":\"response.output_text.delta\",\"delta\":\"x\"}\n\ndata: [DONE]\n\n"
        else:
            body = f"event: response.completed\ndata: {json.dumps(completed)}\n\n"
        return Response(200, text=body, headers={"content-type": "text/event-stream"})

    async def no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("ai_review.clients.openai.v2.client.asyncio.sleep", no_sleep)
    client = OpenAIV2HTTPClient(AsyncClient(
        base_url="https://codex.example/backend-api/codex", transport=MockTransport(handler),
    ))

    response = await client.chat(OpenAIResponsesRequestSchema(model="test", input=[]))

    assert response.first_text == "Замечание"
    assert attempts == 2


@pytest.mark.asyncio
async def test_chat_retries_retryable_sse_failure(monkeypatch):
    attempts = 0
    failed = {
        "type": "response.failed",
        "response": {"status": "failed", "error": {"code": "upstream_unavailable"}},
    }
    completed = {"type": "response.completed", "response": _response_payload()}

    async def handler(_: Request) -> Response:
        nonlocal attempts
        attempts += 1
        payload = failed if attempts == 1 else completed
        body = f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\ndata: [DONE]\n\n"
        return Response(200, text=body, headers={"content-type": "text/event-stream"})

    async def no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("ai_review.clients.openai.v2.client.asyncio.sleep", no_sleep)
    client = OpenAIV2HTTPClient(AsyncClient(
        base_url="https://codex.example/backend-api/codex",
        transport=MockTransport(handler),
    ))

    response = await client.chat(OpenAIResponsesRequestSchema(model="test", input=[]))

    assert response.first_text == "Замечание"
    assert attempts == 2


@pytest.mark.asyncio
async def test_chat_retries_failed_event_without_error_code(monkeypatch):
    attempts = 0
    failed = {"type": "response.failed", "response": {"status": "failed"}}
    completed = {"type": "response.completed", "response": _response_payload()}

    async def handler(_: Request) -> Response:
        nonlocal attempts
        attempts += 1
        payload = failed if attempts == 1 else completed
        body = f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\ndata: [DONE]\n\n"
        return Response(200, text=body, headers={"content-type": "text/event-stream"})

    async def no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("ai_review.clients.openai.v2.client.asyncio.sleep", no_sleep)
    client = OpenAIV2HTTPClient(AsyncClient(
        base_url="https://codex.example/backend-api/codex", transport=MockTransport(handler),
    ))

    response = await client.chat(OpenAIResponsesRequestSchema(model="test", input=[]))

    assert response.first_text == "Замечание"
    assert attempts == 2


@pytest.mark.asyncio
async def test_chat_retries_conflicting_done_event(monkeypatch):
    attempts = 0
    completed = {"type": "response.completed", "response": _response_payload()}

    async def handler(_: Request) -> Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            events = [
                ("response.output_text.done", {"type": "response.output_text.done", "text": "one"}),
                ("response.output_text.done", {"type": "response.output_text.done", "text": "two"}),
            ]
        else:
            events = [("response.completed", completed)]
        body = "".join(
            f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events
        ) + "data: [DONE]\n\n"
        return Response(200, text=body, headers={"content-type": "text/event-stream"})

    async def no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("ai_review.clients.openai.v2.client.asyncio.sleep", no_sleep)
    client = OpenAIV2HTTPClient(AsyncClient(
        base_url="https://codex.example/backend-api/codex", transport=MockTransport(handler),
    ))

    response = await client.chat(OpenAIResponsesRequestSchema(model="test", input=[]))

    assert response.first_text == "Замечание"
    assert attempts == 2


@pytest.mark.asyncio
async def test_chat_retries_mismatched_done_and_delta(monkeypatch):
    attempts = 0
    completed = {"type": "response.completed", "response": _response_payload()}

    async def handler(_: Request) -> Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            events = [
                ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "one"}),
                ("response.output_text.done", {"type": "response.output_text.done", "text": "two"}),
                ("response.completed", completed),
            ]
        else:
            events = [("response.completed", completed)]
        body = "".join(
            f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events
        ) + "data: [DONE]\n\n"
        return Response(200, text=body, headers={"content-type": "text/event-stream"})

    async def no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("ai_review.clients.openai.v2.client.asyncio.sleep", no_sleep)
    client = OpenAIV2HTTPClient(AsyncClient(
        base_url="https://codex.example/backend-api/codex", transport=MockTransport(handler),
    ))

    response = await client.chat(OpenAIResponsesRequestSchema(model="test", input=[]))

    assert response.first_text == "Замечание"
    assert attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("broken_event", ["event_mismatch", "multiple_completed"])
async def test_chat_retries_transient_sse_structure_error(monkeypatch, broken_event):
    attempts = 0
    completed = {"type": "response.completed", "response": _response_payload()}

    async def handler(_: Request) -> Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1 and broken_event == "event_mismatch":
            body = (
                "event: response.output_text.delta\n"
                "data: {\"type\": \"response.output_text.done\", \"text\": \"x\"}\n\n"
            )
        elif attempts == 1:
            body = "".join(
                f"event: response.completed\ndata: {json.dumps(completed)}\n\n" for _ in range(2)
            )
        else:
            body = f"event: response.completed\ndata: {json.dumps(completed)}\n\n"
        return Response(200, text=body, headers={"content-type": "text/event-stream"})

    async def no_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("ai_review.clients.openai.v2.client.asyncio.sleep", no_sleep)
    client = OpenAIV2HTTPClient(AsyncClient(
        base_url="https://codex.example/backend-api/codex", transport=MockTransport(handler),
    ))

    response = await client.chat(OpenAIResponsesRequestSchema(model="test", input=[]))

    assert response.first_text == "Замечание"
    assert attempts == 2


@pytest.mark.asyncio
async def test_chat_reconstructs_text_when_completed_output_is_empty():
    completed = {
        "type": "response.completed",
        "response": {"usage": {"total_tokens": 3, "input_tokens": 2, "output_tokens": 1}, "output": []},
    }
    events = [
        ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "Заме"}),
        ("response.output_text.delta", {"type": "response.output_text.delta", "delta": "чание"}),
        ("response.output_text.done", {"type": "response.output_text.done", "text": "Замечание"}),
        ("response.completed", completed),
    ]
    body = "".join(
        f"event: {name}\ndata: {json.dumps(payload)}\n\n" for name, payload in events
    ) + "data: [DONE]\n\n"

    async def handler(_: Request) -> Response:
        return Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = OpenAIV2HTTPClient(AsyncClient(
        base_url="https://codex.example/backend-api/codex", transport=MockTransport(handler),
    ))
    response = await client.chat(OpenAIResponsesRequestSchema(model="test", input=[]))

    assert response.first_text == "Замечание"
