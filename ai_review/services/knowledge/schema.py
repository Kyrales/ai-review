import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


ReplyId = Annotated[str, StringConstraints(min_length=1, max_length=256)]
Quote = Annotated[str, StringConstraints(min_length=1, max_length=1000)]
Rule = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
Rationale = Annotated[str, StringConstraints(min_length=1, max_length=4000)]


def normalize_knowledge_text(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


class EligibleKnowledgeSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reply_id: ReplyId
    username: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    text: Annotated[str, StringConstraints(min_length=1, max_length=12000)]

    @field_validator("reply_id", "username")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        value = normalize_knowledge_text(value).strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("text")
    @classmethod
    def normalize_source_text(cls, value: str) -> str:
        value = normalize_knowledge_text(value)
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


class KnowledgeExtractionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    original_finding: Annotated[
        str, StringConstraints(min_length=1, max_length=12000)
    ]
    current_code: Annotated[str, StringConstraints(max_length=12000)]
    current_diff: Annotated[str, StringConstraints(max_length=12000)]

    @field_validator("original_finding")
    @classmethod
    def normalize_finding(cls, value: str) -> str:
        value = normalize_knowledge_text(value).strip()
        if not value:
            raise ValueError("original_finding must not be blank")
        return value

    @field_validator("current_code", "current_diff")
    @classmethod
    def normalize_context(cls, value: str) -> str:
        return normalize_knowledge_text(value)


class KnowledgeCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_reply_id: ReplyId
    source_quote: Quote
    knowledge_type: Literal["false_positive", "correction", "new_check"] = Field(alias="type")
    rule: Rule
    rationale: Rationale

    @field_validator("source_reply_id", "rule", "rationale")
    @classmethod
    def normalize_trimmed_text(cls, value: str) -> str:
        value = normalize_knowledge_text(value).strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("source_quote")
    @classmethod
    def normalize_quote(cls, value: str) -> str:
        value = normalize_knowledge_text(value)
        if not value.strip():
            raise ValueError("value must not be blank")
        return value
