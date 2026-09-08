import json

import pytest
from httpx import AsyncClient, MockTransport, Request, Response

from ai_review.clients.openai.v2.client import (
    get_openai_v2_http_client,
    OpenAIV2HTTPClient,
    OpenAIV2ProtocolError,
)
from ai_review.clients.openai.v2.schema import OpenAIResponsesRequestSchema


@pytest.mark.usefixtures('openai_v2_http_client_config')
def test_get_openai_v2_http_client_builds_ok():
    openai_http_client = get_openai_v2_http_client()

    assert isinstance(openai_http_client, OpenAIV2HTTPClient)
    assert isinstance(openai_http_client.client, AsyncClient)


def _response_payload(text: str = "Замечание") -> dict:
    return {
        "usage": {"total_tokens": 3, "input_tokens": 2, "output_tokens": 1},
        "output": [{
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        }],
    }


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
