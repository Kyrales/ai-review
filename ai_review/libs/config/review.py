from enum import StrEnum

from pydantic import BaseModel, Field


class ReviewMode(StrEnum):
    FULL_FILE_DIFF = "FULL_FILE_DIFF"
    FULL_FILE_CURRENT = "FULL_FILE_CURRENT"
    FULL_FILE_PREVIOUS = "FULL_FILE_PREVIOUS"

    ONLY_ADDED = "ONLY_ADDED"
    ONLY_REMOVED = "ONLY_REMOVED"
    ADDED_AND_REMOVED = "ADDED_AND_REMOVED"

    ONLY_ADDED_WITH_CONTEXT = "ONLY_ADDED_WITH_CONTEXT"
    ONLY_REMOVED_WITH_CONTEXT = "ONLY_REMOVED_WITH_CONTEXT"
    ADDED_AND_REMOVED_WITH_CONTEXT = "ADDED_AND_REMOVED_WITH_CONTEXT"


class ReviewSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReviewConfig(BaseModel):
    mode: ReviewMode = ReviewMode.FULL_FILE_DIFF
    dry_run: bool = False
    inline_tag: str = Field(default="#ai-review-inline")
    inline_reply_tag: str = Field(default="#ai-review-inline-reply")
    inline_fallback_tag: str = Field(default="#ai-review-inline-fallback")
    summary_tag: str = Field(default="#ai-review-summary")
    summary_reply_tag: str = Field(default="#ai-review-summary-reply")
    context_lines: int = Field(default=10, ge=0)
    allow_changes: list[str] = Field(default_factory=list)
    ignore_changes: list[str] = Field(default_factory=list)
    ignore_pure_renames: bool = True
    max_inline_comments: int | None = None
    max_context_comments: int | None = None
    publish_severities: list[ReviewSeverity] = Field(default_factory=lambda: [
        ReviewSeverity.CRITICAL,
        ReviewSeverity.HIGH,
        ReviewSeverity.MEDIUM,
    ])
    max_inline_prompt_chars: int = Field(default=100_000, ge=1000)
    inline_comment_fallback: bool = True
    ignore_1c_role_restriction_templates: list[str] = Field(default_factory=list)
