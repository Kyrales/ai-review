from typing import Literal

from pydantic import BaseModel, Field


class FollowupReply(BaseModel):
    verdict: Literal["fixed", "open", "clarify", "withdrawn"]
    message: str = Field(min_length=1)
    suggestion: str | None = None
    silent: bool = False
    silent_source_reply_id: str | None = None
