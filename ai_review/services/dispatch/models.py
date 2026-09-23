from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

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
