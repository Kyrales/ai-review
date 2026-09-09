from pydantic import BaseModel, SecretStr

from ai_review.libs.config.http import HTTPClientWithTokenConfig


class GitFlicPipelineConfig(BaseModel):
    owner: str
    project: str
    merge_request_id: int


class GitFlicHTTPClientConfig(HTTPClientWithTokenConfig):
    api_token_fallback: SecretStr | None = None

    @property
    def api_token_fallback_value(self) -> str | None:
        return (
            self.api_token_fallback.get_secret_value()
            if self.api_token_fallback
            else None
        )
