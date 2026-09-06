from pydantic import BaseModel

from ai_review.libs.config.http import HTTPClientWithTokenConfig


class GitFlicPipelineConfig(BaseModel):
    owner: str
    project: str
    merge_request_id: int


class GitFlicHTTPClientConfig(HTTPClientWithTokenConfig):
    pass
