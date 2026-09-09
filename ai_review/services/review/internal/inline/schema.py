from typing import Self

from pydantic import BaseModel, Field, PrivateAttr, RootModel, field_validator

from ai_review.config import settings
from ai_review.libs.config.review import ReviewSeverity

DedupKey = tuple[str, int, str]


class InlineCommentSchema(BaseModel):
    _body_is_rendered: bool = PrivateAttr(default=False)

    file: str = Field(min_length=1)
    line: int = Field(ge=1)
    message: str = Field(min_length=1)
    suggestion: str | None = None
    severity: ReviewSeverity = ReviewSeverity.MEDIUM

    @field_validator("file")
    def normalize_file(cls, value: str) -> str:
        value = value.strip().replace("\\", "/")
        return value.lstrip("/")

    @field_validator("message")
    def normalize_message(cls, value: str) -> str:
        return value.strip()

    @property
    def dedup_key(self) -> DedupKey:
        return self.file, self.line, (self.suggestion or self.message).strip().lower()

    @property
    def body(self) -> str:
        if self._body_is_rendered:
            return self.message
        severity = f"**Критичность: {self.severity}**\n\n"
        if self.suggestion:
            return f"{severity}{self.message}\n\n```suggestion\n{self.suggestion}\n```"

        return f"{severity}{self.message}"

    def replace_with_rendered_body(self, body: str) -> None:
        self.message = body
        self.suggestion = None
        self._body_is_rendered = True

    @property
    def body_with_tag(self) -> str:
        return f"{self.body}\n\n{settings.review.inline_tag}"

    @property
    def fallback_body(self) -> str:
        return f"**{self.file}:{self.line}** — {self.body}"


class InlineCommentListSchema(RootModel[list[InlineCommentSchema]]):
    root: list[InlineCommentSchema]

    def dedupe(self) -> Self:
        results_map: dict[DedupKey, InlineCommentSchema] = {
            comment.dedup_key: comment for comment in self.root
        }

        return InlineCommentListSchema(root=list(results_map.values()))
