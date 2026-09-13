import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import HttpUrl, SecretStr, ValidationError

from ai_review.clients.openai.v2.schema import (
    OpenAIResponseContentSchema,
    OpenAIResponseOutputSchema,
    OpenAIResponsesResponseSchema,
    OpenAIResponseUsageSchema,
)
from ai_review.libs.config.llm.openai import OpenAIHTTPClientConfig
from ai_review.services.knowledge.llm_gateway import (
    KnowledgeLLMGateway,
    SyncLLMSettings,
)


_ENV_NAMES = (
    "AI_REVIEW_API_URL",
    "AI_REVIEW_API_TOKEN",
    "AI_REVIEW_MODEL",
    "AI_REVIEW_REASONING_EFFORT",
)


class _FakeClient:
    def __init__(self) -> None:
        self.requests = []

    async def chat(self, request):
        self.requests.append(request)
        return OpenAIResponsesResponseSchema(
            usage=OpenAIResponseUsageSchema(
                total_tokens=3,
                input_tokens=2,
                output_tokens=1,
            ),
            output=[
                OpenAIResponseOutputSchema(
                    type="message",
                    role="assistant",
                    content=[
                        OpenAIResponseContentSchema(
                            type="output_text",
                            text="compiled rule",
                        )
                    ],
                )
            ],
        )


def _clear_sync_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_sync_settings_environment_overrides_repository_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_sync_environment(monkeypatch)
    (tmp_path / ".env").write_text(
        "AI_REVIEW_API_URL=https://dotenv.example/backend\n"
        "AI_REVIEW_API_TOKEN=dotenv-token\n"
        "AI_REVIEW_MODEL=dotenv-model\n"
        "AI_REVIEW_REASONING_EFFORT=low\n"
        "UNRELATED_SECRET=must-not-be-read\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_REVIEW_API_URL", "https://env.example/api")
    monkeypatch.setenv("AI_REVIEW_API_TOKEN", "environment-token")
    monkeypatch.setenv("AI_REVIEW_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("AI_REVIEW_REASONING_EFFORT", "high")

    settings = SyncLLMSettings.from_environment(tmp_path)

    assert str(settings.api_url).rstrip("/") == "https://env.example/api"
    assert settings.api_token.get_secret_value() == "environment-token"
    assert settings.model == "gpt-5.6-sol"
    assert settings.reasoning_effort == "high"
    assert settings.timeout == 2700.0
    assert "environment-token" not in repr(settings)
    assert "environment-token" not in settings.model_dump_json()
    assert "api_token" not in settings.diagnostics


def test_sync_settings_reads_repository_dotenv_when_environment_is_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_sync_environment(monkeypatch)
    (tmp_path / ".env").write_text(
        "AI_REVIEW_API_URL='https://dotenv.example/backend'\n"
        "AI_REVIEW_API_TOKEN=dotenv-token\n"
        "AI_REVIEW_MODEL=dotenv-model\n"
        "AI_REVIEW_REASONING_EFFORT=xhigh\n",
        encoding="utf-8",
    )

    settings = SyncLLMSettings.from_environment(tmp_path)

    assert str(settings.api_url).rstrip("/") == "https://dotenv.example/backend"
    assert settings.api_token.get_secret_value() == "dotenv-token"
    assert settings.model == "dotenv-model"
    assert settings.reasoning_effort == "xhigh"


def test_sync_settings_empty_environment_value_does_not_fall_back_to_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _clear_sync_environment(monkeypatch)
    (tmp_path / ".env").write_text(
        "AI_REVIEW_API_URL=https://dotenv.example/backend\n"
        "AI_REVIEW_API_TOKEN=dotenv-token\n"
        "AI_REVIEW_MODEL=dotenv-model\n"
        "AI_REVIEW_REASONING_EFFORT=low\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AI_REVIEW_API_TOKEN", "")

    with pytest.raises(ValidationError):
        SyncLLMSettings.from_environment(tmp_path)


def test_sync_gateway_import_does_not_require_global_review_settings(tmp_path: Path):
    repository_root = Path(__file__).resolve().parents[5]
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("LLM__", "VCS__", "AI_REVIEW_CONFIG_FILE"))
    }
    environment["PYTHONPATH"] = str(repository_root)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from ai_review.services.knowledge.llm_gateway "
                "import SyncLLMSettings; print(SyncLLMSettings.__name__)"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "SyncLLMSettings"


@pytest.mark.asyncio
async def test_gateway_builds_one_non_streaming_responses_request():
    client = _FakeClient()
    settings = SyncLLMSettings(
        api_url=HttpUrl("https://codex.example/backend-api/codex"),
        api_token=SecretStr("secret-token"),
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    gateway = KnowledgeLLMGateway(settings=settings, client=client)

    result = await gateway.ask(
        instructions="Compile reusable rules.",
        input_text="untrusted reviewer data",
        max_output_tokens=4096,
    )

    assert result == "compiled rule"
    assert len(client.requests) == 1
    assert client.requests[0].model_dump(exclude_none=True) == {
        "model": "gpt-5.6-sol",
        "input": [{"role": "user", "content": "untrusted reviewer data"}],
        "stream": False,
        "reasoning": {"effort": "high"},
        "instructions": "Compile reusable rules.",
        "max_output_tokens": 4096,
    }


@pytest.mark.asyncio
async def test_gateway_does_not_retry_client_errors():
    class FailingClient:
        def __init__(self) -> None:
            self.calls = 0

        async def chat(self, request):
            self.calls += 1
            raise RuntimeError("transport failed")

    client = FailingClient()
    settings = SyncLLMSettings(
        api_url=HttpUrl("https://codex.example"),
        api_token=SecretStr("secret-token"),
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )
    gateway = KnowledgeLLMGateway(settings=settings, client=client)

    with pytest.raises(RuntimeError, match="transport failed"):
        await gateway.ask("instructions", "input", 100)

    assert client.calls == 1


def test_gateway_creates_explicit_http_config(monkeypatch: pytest.MonkeyPatch):
    captured = {}

    def fake_factory(*, config, timeout):
        captured.update(config=config, timeout=timeout)
        return _FakeClient()

    monkeypatch.setattr(
        "ai_review.services.knowledge.llm_gateway.get_openai_v2_http_client",
        fake_factory,
    )
    settings = SyncLLMSettings(
        api_url=HttpUrl("https://codex.example/backend-api/codex"),
        api_token=SecretStr("secret-token"),
        model="gpt-5.6-sol",
        reasoning_effort="high",
    )

    KnowledgeLLMGateway(settings=settings)

    assert isinstance(captured["config"], OpenAIHTTPClientConfig)
    assert captured["config"].api_token.get_secret_value() == "secret-token"
    assert captured["timeout"] == 2700.0


def test_environment_gateway_defers_llm_settings_until_first_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_REVIEW_API_URL", raising=False)
    monkeypatch.delenv("AI_REVIEW_API_TOKEN", raising=False)
    monkeypatch.delenv("AI_REVIEW_MODEL", raising=False)
    monkeypatch.delenv("AI_REVIEW_REASONING_EFFORT", raising=False)

    gateway = KnowledgeLLMGateway(repository_root=tmp_path)

    assert gateway.settings is None
    assert gateway.client is None
