from typing import Literal

from pydantic import BaseModel, Field


class FollowupReply(BaseModel):
    verdict: Literal["fixed", "open", "clarify"]
    message: str = Field(min_length=1)
    suggestion: str | None = None
