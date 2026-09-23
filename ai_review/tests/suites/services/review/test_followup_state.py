from ai_review.services.review.followup_state import FollowupStateAnalyzer
from ai_review.services.vcs.markers import MarkerKind, ReviewMarker, decorate_ai_message
from ai_review.services.vcs.types import ReviewCommentSchema, ReviewThreadSchema, ThreadKind, UserSchema


HEAD = "a" * 40
TRUSTED = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"


def comment(identifier: str, body: str, *, author: str = "human", parent: str | None = "thread") -> ReviewCommentSchema:
    return ReviewCommentSchema(id=identifier, body=body, parent_id=parent, author=UserSchema(id=author))


def thread(identifier: str, comments: list[ReviewCommentSchema], *, resolved: bool = False) -> ReviewThreadSchema:
    return ReviewThreadSchema(id=identifier, kind=ThreadKind.INLINE, comments=comments, resolved=resolved)


def followup(*covered: str, origin: str = "thread", verdict: str = "open") -> str:
    return decorate_ai_message(
        "reply",
        ReviewMarker(
            version="v2", kind=MarkerKind.FOLLOWUP, head=HEAD,
            covered=tuple(covered), verdict=verdict, origin=origin,
            publication="b" * 64,
        ),
    )


def test_continuation_covers_origin_reply_with_normalized_opaque_id() -> None:
    analyzer = FollowupStateAnalyzer(TRUSTED)
    origin = thread("thread", [comment("e\u0301", "human")])
    continuation = thread(
        "continuation",
        [comment("ai", followup("é"), author=TRUSTED.lower())],
    )

    assert analyzer.pending_comments(origin, (continuation,)) == ()
    assert analyzer.continuations(origin, (origin, continuation)) == (continuation,)


def test_foreign_marker_like_comment_remains_pending() -> None:
    analyzer = FollowupStateAnalyzer(TRUSTED)
    reply = comment("reply", followup("older"), author="someone-else")

    assert analyzer.pending_ids(thread("thread", [reply])) == ("reply",)


def test_terminal_followup_requires_resolve_only_while_unresolved() -> None:
    analyzer = FollowupStateAnalyzer(TRUSTED)
    fixed = comment("ai", followup("reply", verdict="fixed"), author=TRUSTED.lower())
    unresolved = thread("thread", [fixed])
    resolved = thread("thread", [fixed], resolved=True)

    assert analyzer.last_followup_requires_resolve(unresolved) is True
    assert analyzer.resolve_thread_ids(unresolved) == ("thread",)
    assert analyzer.resolve_thread_ids(resolved) == ()


def test_damaged_marker_is_treated_as_human_reply() -> None:
    analyzer = FollowupStateAnalyzer(TRUSTED)
    damaged = comment("reply", "<!-- ai-review:v2;broken -->", author=TRUSTED)

    assert analyzer.pending_ids(thread("thread", [damaged])) == ("reply",)
