import pytest

from ai_review.services.llm.types import ChatResultSchema
from ai_review.services.review.gateway.review_direct_llm_gateway import ReviewDirectLLMGateway
from ai_review.tests.fixtures.services.artifacts import FakeArtifactsService
from ai_review.tests.fixtures.services.cost import FakeCostService
from ai_review.tests.fixtures.services.llm import FakeLLMClient


@pytest.mark.asyncio
async def test_ask_happy_path(
        review_direct_llm_gateway: ReviewDirectLLMGateway,
        fake_llm_client: FakeLLMClient,
        fake_cost_service: FakeCostService,
        fake_artifacts_service: FakeArtifactsService,
):
    """Should call LLM, calculate cost, save artifacts, and return text."""
    fake_llm_client.responses["chat"] = ChatResultSchema(text="FAKE_RESPONSE")

    result = await review_direct_llm_gateway.ask("PROMPT", "SYSTEM_PROMPT")

    assert result == "FAKE_RESPONSE"
    assert any(call[0] == "chat" for call in fake_llm_client.calls)
    calculate_calls = [call for call in fake_cost_service.calls if call[0] == "calculate"]
    assert len(calculate_calls) == 1
    assert calculate_calls[0][1]["result"].prompt_tokens is None
    assert calculate_calls[0][1]["result"].completion_tokens is None
    assert any(call[0] == "save_llm" for call in fake_artifacts_service.calls)


@pytest.mark.asyncio
async def test_ask_rejects_empty_response(
        capsys: pytest.CaptureFixture,
        review_direct_llm_gateway: ReviewDirectLLMGateway,
        fake_llm_client: FakeLLMClient,
        fake_cost_service: FakeCostService,
        fake_artifacts_service: FakeArtifactsService,
):
    """Should warn if LLM returns an empty response."""
    fake_llm_client.responses["chat"] = ChatResultSchema(text="")

    with pytest.raises(RuntimeError, match="empty response"):
        await review_direct_llm_gateway.ask("PROMPT", "SYSTEM_PROMPT")
    output = capsys.readouterr().out

    assert "LLM returned an empty response" in output

    assert any(call[0] == "chat" for call in fake_llm_client.calls)
    assert not any(call[0] == "calculate" for call in fake_cost_service.calls)
    assert not any(call[0] == "save_llm" for call in fake_artifacts_service.calls)


@pytest.mark.asyncio
async def test_ask_retries_empty_response_before_success(
        monkeypatch,
        review_direct_llm_gateway: ReviewDirectLLMGateway,
        fake_llm_client: FakeLLMClient,
):
    responses = iter([ChatResultSchema(text=""), ChatResultSchema(text="OK")])
    async def chat(*_args, **_kwargs):
        return next(responses)

    fake_llm_client.chat = chat

    async def sleep(*_args):
        return None

    monkeypatch.setattr("ai_review.services.review.gateway.review_direct_llm_gateway.asyncio.sleep", sleep)

    assert await review_direct_llm_gateway.ask("PROMPT", "SYSTEM_PROMPT") == "OK"


@pytest.mark.asyncio
async def test_ask_retries_whitespace_response_before_success(
        monkeypatch,
        review_direct_llm_gateway: ReviewDirectLLMGateway,
        fake_llm_client: FakeLLMClient,
):
    responses = iter([ChatResultSchema(text="  \n"), ChatResultSchema(text="OK")])

    async def chat(*_args, **_kwargs):
        return next(responses)

    fake_llm_client.chat = chat

    async def sleep(*_args):
        return None

    monkeypatch.setattr("ai_review.services.review.gateway.review_direct_llm_gateway.asyncio.sleep", sleep)

    assert await review_direct_llm_gateway.ask("PROMPT", "SYSTEM_PROMPT") == "OK"


@pytest.mark.asyncio
async def test_ask_rejects_three_whitespace_responses(
        monkeypatch,
        review_direct_llm_gateway: ReviewDirectLLMGateway,
        fake_llm_client: FakeLLMClient,
):
    calls = 0

    async def chat(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return ChatResultSchema(text=" \n")

    fake_llm_client.chat = chat

    async def sleep(*_args):
        return None

    monkeypatch.setattr("ai_review.services.review.gateway.review_direct_llm_gateway.asyncio.sleep", sleep)

    with pytest.raises(RuntimeError, match="empty response"):
        await review_direct_llm_gateway.ask("PROMPT", "SYSTEM_PROMPT")
    assert calls == 3


@pytest.mark.asyncio
async def test_ask_passes_llm_tokens_to_calculate(
        review_direct_llm_gateway: ReviewDirectLLMGateway,
        fake_llm_client: FakeLLMClient,
        fake_cost_service: FakeCostService,
):
    fake_llm_client.responses["chat"] = ChatResultSchema(
        text="FAKE_RESPONSE",
        prompt_tokens=123,
        completion_tokens=77,
    )

    result = await review_direct_llm_gateway.ask("PROMPT", "SYSTEM_PROMPT")

    assert result == "FAKE_RESPONSE"
    calculate_call = next(call for call in fake_cost_service.calls if call[0] == "calculate")
    assert calculate_call[1]["result"].prompt_tokens == 123
    assert calculate_call[1]["result"].completion_tokens == 77


@pytest.mark.asyncio
async def test_ask_propagates_llm_error(
        capsys: pytest.CaptureFixture,
        fake_llm_client: FakeLLMClient,
        review_direct_llm_gateway: ReviewDirectLLMGateway,
):
    """Should handle exceptions gracefully and log error."""

    async def failing_chat(prompt: str, prompt_system: str):
        raise RuntimeError("LLM connection failed")

    fake_llm_client.chat = failing_chat

    with pytest.raises(RuntimeError, match="LLM connection failed"):
        await review_direct_llm_gateway.ask("PROMPT", "SYSTEM_PROMPT")
    output = capsys.readouterr().out

    assert "LLM request failed" in output
    assert "RuntimeError" in output
