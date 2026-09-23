import pytest

from ai_review.services.dispatch.models import DispatchMode, classify_dispatch
from ai_review.services.vcs.markers import MarkerKind, ReviewMarker, decorate_ai_message
from ai_review.services.vcs.types import ReviewCommentSchema, ReviewThreadSchema, ThreadKind, UserSchema


HEAD = "a" * 40
OLD = "b" * 40
TRUSTED = "reviewer"


def marked(kind: MarkerKind, head: str = HEAD, **values: object) -> str:
    return decorate_ai_message("AI", ReviewMarker(kind=kind, head=head, **values))


def thread(kind: ThreadKind, root_body: str, *replies: ReviewCommentSchema, resolved: bool = False) -> ReviewThreadSchema:
    return ReviewThreadSchema(
        id=f"{kind.value}-thread", kind=kind, resolved=resolved,
        comments=[ReviewCommentSchema(id="root", body=root_body, author=UserSchema(id=TRUSTED)), *replies],
    )


@pytest.mark.parametrize(
    ("threads", "mode"),
    [
        ((), DispatchMode.INITIAL),
        ((thread(ThreadKind.INLINE, marked(MarkerKind.FINDING)),), DispatchMode.INITIAL),
        ((thread(ThreadKind.SUMMARY, marked(MarkerKind.SUMMARY, status="complete")),), DispatchMode.NONE),
        ((thread(ThreadKind.SUMMARY, marked(MarkerKind.SUMMARY, OLD, status="complete")),), DispatchMode.INITIAL),
        ((thread(ThreadKind.INLINE, marked(MarkerKind.SUMMARY, status="complete")),), DispatchMode.INITIAL),
        ((thread(ThreadKind.SUMMARY, marked(MarkerKind.FINDING)),), DispatchMode.INITIAL),
    ],
)
def test_classify_dispatch_lifecycle(threads, mode) -> None:
    assert classify_dispatch(threads, TRUSTED, HEAD).mode is mode


def test_classify_dispatch_prefers_uncovered_followup_over_stale_head() -> None:
    reply = ReviewCommentSchema(id="reply", body="fixed", parent_id="INLINE-thread")
    decision = classify_dispatch(
        (thread(ThreadKind.INLINE, marked(MarkerKind.FINDING, OLD), reply),),
        TRUSTED,
        HEAD,
    )

    assert (decision.mode, decision.pending_count) == (DispatchMode.FOLLOWUP, 1)


def test_classify_dispatch_retries_unresolved_terminal_followup() -> None:
    followup = ReviewCommentSchema(
        id="followup", parent_id="INLINE-thread", author=UserSchema(id=TRUSTED),
        body=marked(
            MarkerKind.FOLLOWUP,
            covered=("11111111-1111-4111-8111-111111111111",),
            verdict="fixed",
        ),
    )
    decision = classify_dispatch(
        (thread(ThreadKind.INLINE, marked(MarkerKind.FINDING), followup),),
        TRUSTED,
        HEAD,
    )

    assert (decision.mode, decision.pending_count) == (DispatchMode.FOLLOWUP, 0)
