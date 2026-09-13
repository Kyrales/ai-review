from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, SecretStr

from ai_review.clients.openai.v2.client import get_openai_v2_http_client
from ai_review.clients.openai.v2.schema import (
    OpenAIInputMessageSchema,
    OpenAIReasoningSchema,
    OpenAIResponsesRequestSchema,
)
from ai_review.clients.openai.v2.types import OpenAIV2HTTPClientProtocol
from ai_review.libs.config.llm.openai import OpenAIHTTPClientConfig


class SyncLLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_url: HttpUrl
    api_token: SecretStr = Field(min_length=1, exclude=True, repr=False)
    model: str = Field(min_length=1)
    reasoning_effort: Literal[
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    timeout: float = 2700.0

    @classmethod
    def from_environment(cls, repository_root: Path) -> "SyncLLMSettings":
        names = {
            "api_url": "AI_REVIEW_API_URL",
            "api_token": "AI_REVIEW_API_TOKEN",
            "model": "AI_REVIEW_MODEL",
            "reasoning_effort": "AI_REVIEW_REASONING_EFFORT",
        }
        dotenv = dotenv_values(repository_root / ".env")
        values = {
            field: (
                os.environ[environment_name]
                if environment_name in os.environ
                else dotenv.get(environment_name)
            )
            for field, environment_name in names.items()
        }
        return cls.model_validate(values)

    @property
    def diagnostics(self) -> dict[str, str | float]:
        return {
            "api_url": str(self.api_url),
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "timeout": self.timeout,
        }

    def http_client_config(self) -> OpenAIHTTPClientConfig:
        return OpenAIHTTPClientConfig(
            api_url=self.api_url,
            api_token=self.api_token,
            timeout=self.timeout,
        )


class KnowledgeLLMGateway:
    def __init__(
        self,
        settings: SyncLLMSettings | None = None,
        client: OpenAIV2HTTPClientProtocol | None = None,
        *,
        repository_root: Path | None = None,
    ) -> None:
        if settings is None and repository_root is None:
            raise ValueError("settings or repository_root is required")
        self.settings = settings
        self.client = client or (
            get_openai_v2_http_client(
                config=settings.http_client_config(),
                timeout=settings.timeout,
            )
            if settings is not None
            else None
        )
        self.repository_root = repository_root

    def _configure(self) -> tuple[SyncLLMSettings, OpenAIV2HTTPClientProtocol]:
        if self.settings is None:
            self.settings = SyncLLMSettings.from_environment(self.repository_root)
        if self.client is None:
            self.client = get_openai_v2_http_client(
                config=self.settings.http_client_config(),
                timeout=self.settings.timeout,
            )
        return self.settings, self.client

    async def ask(
        self,
        instructions: str,
        input_text: str,
        max_output_tokens: int,
    ) -> str:
        settings, client = self._configure()
        request = OpenAIResponsesRequestSchema(
            model=settings.model,
            input=[OpenAIInputMessageSchema(role="user", content=input_text)],
            instructions=instructions,
            stream=False,
            reasoning=OpenAIReasoningSchema(
                effort=settings.reasoning_effort,
            ),
            max_output_tokens=max_output_tokens,
        )
        response = await client.chat(request)
        return response.first_text
