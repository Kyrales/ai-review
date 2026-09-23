from collections.abc import Sequence
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_review.services.review.followup_state import FollowupStateAnalyzer
from ai_review.services.vcs.markers import MarkerKind
from ai_review.services.vcs.types import ReviewThreadSchema, ThreadKind


class DispatchMode(StrEnum):
    INITIAL = "initial"
    FOLLOWUP = "followup"
    NONE = "none"


class DispatchDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: DispatchMode
    reason: str
    pending_count: int = Field(ge=0)


class DispatchSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    all_open: bool = False
    merge_request_ids: tuple[int, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def normalize_ids(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        result = dict(values)
        result["merge_request_ids"] = tuple(dict.fromkeys(result.get("merge_request_ids") or ()))
        return result

    @model_validator(mode="after")
    def validate_selection(self) -> "DispatchSelection":
        if self.all_open == bool(self.merge_request_ids):
            raise ValueError("select --all or one or more merge request IDs")
        if len(self.merge_request_ids) > 10 or any(value <= 0 for value in self.merge_request_ids):
            raise ValueError("merge request IDs must contain 1-10 positive unique values")
        return self


class DispatchItemResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mr_id: int
    mode: DispatchMode | None
    outcome: Literal["started", "skipped", "failed"]
    reason: str
    pipeline_id: str | None = None


class DispatchReport(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["success", "partial", "fatal"]
    failure_scope: Literal["none", "item", "source", "security"]
    error_code: str | None = None
    selected_count: int
    started_count: int
    skipped_count: int
    failed_count: int
    items: tuple[DispatchItemResult, ...]


def classify_dispatch(
    threads: Sequence[ReviewThreadSchema],
    trusted_author_id: str,
    current_head: str,
) -> DispatchDecision:
    analyzer = FollowupStateAnalyzer(trusted_author_id)
    related = tuple(threads)
    started: list[ReviewThreadSchema] = []
    for thread in threads:
        root = thread.comments[0] if thread.comments else None
        marker = analyzer.marker(root) if root is not None else None
        if marker is None:
            continue
        if (
            thread.kind is ThreadKind.INLINE and marker.kind is MarkerKind.FINDING
            or thread.kind is ThreadKind.SUMMARY and marker.kind is MarkerKind.SUMMARY
        ):
            started.append(thread)

    pending_count = sum(len(analyzer.pending_comments(thread, related)) for thread in started)
    requires_resolve = any(analyzer.resolve_thread_ids(thread, related) for thread in started)
    if pending_count or requires_resolve:
        return DispatchDecision(
            mode=DispatchMode.FOLLOWUP,
            reason="pending_replies" if pending_count else "resolve_required",
            pending_count=pending_count,
        )

    for thread in started:
        if thread.kind is not ThreadKind.SUMMARY or not thread.comments:
            continue
        marker = analyzer.marker(thread.comments[0])
        if marker is not None and marker.head == current_head and marker.status is not None:
            return DispatchDecision(
                mode=DispatchMode.NONE,
                reason="current_terminal_summary",
                pending_count=0,
            )
    return DispatchDecision(
        mode=DispatchMode.INITIAL,
        reason="initial_or_recovery_required",
        pending_count=0,
    )
