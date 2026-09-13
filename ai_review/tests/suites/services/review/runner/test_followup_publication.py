from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_review.services.review.runner.followup_publication import (
    RESTORE_RESOLVED_MARKER,
    FollowupPublicationStateMachine,
    needs_resolve_restore,
)
from ai_review.services.vcs.markers import MarkerKind, ReviewMarker, decorate_ai_message
from ai_review.services.vcs.types import ReviewCommentSchema, ReviewThreadSchema, ThreadKind, UserSchema


def item(thread_id: str, *, resolved: bool | None, body: str = "root") -> ReviewThreadSchema:
    return ReviewThreadSchema(
        id=thread_id,
        kind=ThreadKind.INLINE,
        resolved=resolved,
        comments=[ReviewCommentSchema(id=f"{thread_id}-root", body=body, author=UserSchema(id="owner"))],
    )


def test_restore_marker_only_applies_to_latest_trusted_followup():
    """Catches a stale recovery marker reclosing a discussion after a newer followup."""
    old = ReviewMarker(
        version="v2", kind=MarkerKind.FOLLOWUP, head="a" * 40,
        covered=("old",), verdict="clarify", origin="origin", publication="1" * 64,
    )
    current = ReviewMarker(
        version="v2", kind=MarkerKind.FOLLOWUP, head="a" * 40,
        covered=("new",), verdict="open", origin="origin", publication="2" * 64,
    )
    thread = ReviewThreadSchema(
        id="origin", kind=ThreadKind.INLINE, resolved=False,
        comments=[
            ReviewCommentSchema(
                id="old", body=decorate_ai_message("old", old) + f"\n\n{RESTORE_RESOLVED_MARKER}",
                author=UserSchema(id="owner"),
            ),
            ReviewCommentSchema(
                id="new", body=decorate_ai_message("new", current),
                author=UserSchema(id="owner"),
            ),
        ],
    )

    assert needs_resolve_restore(thread, "owner") is False


@pytest.mark.asyncio
async def test_closed_thread_reopens_posts_and_restores_closed_state():
    """Catches posting to a resolved thread without capability recovery."""
    origin = item("origin", resolved=True)
    opened = item("origin", resolved=False)
    vcs = SimpleNamespace(
        can_reply_resolved=False,
        can_reopen=True,
        reopen_thread=AsyncMock(),
        resolve_thread=AsyncMock(),
        create_inline_reply=AsyncMock(),
        get_inline_threads=AsyncMock(side_effect=[[origin], [opened], [opened]]),
        get_general_threads=AsyncMock(return_value=[]),
    )
    machine = FollowupPublicationStateMachine(vcs, "owner")

    await machine.publish(origin, "message", "a" * 64)

    vcs.reopen_thread.assert_awaited_once_with("origin")
    posted = vcs.create_inline_reply.await_args.args[1]
    assert posted.startswith("message") and RESTORE_RESOLVED_MARKER in posted
    vcs.resolve_thread.assert_awaited_once_with("origin")


@pytest.mark.asyncio
async def test_unknown_resolved_uses_continuation_and_closes_it():
    """Catches unsafe reopen when the provider did not report resolved state."""
    origin = item("origin", resolved=None)
    marker = ReviewMarker(
        version="v2", kind=MarkerKind.FOLLOWUP, head="a" * 40,
        covered=("reply",), verdict="open", origin="origin", publication="a" * 64,
    )
    continuation = item(
        "continuation", resolved=False, body=decorate_ai_message("answer", marker)
    )
    vcs = SimpleNamespace(
        can_reply_resolved=False,
        can_reopen=True,
        reopen_thread=AsyncMock(),
        resolve_thread=AsyncMock(),
        create_continuation_thread=AsyncMock(return_value=continuation),
        get_inline_threads=AsyncMock(side_effect=[[origin], [origin]]),
        get_general_threads=AsyncMock(side_effect=[[], [continuation]]),
    )
    machine = FollowupPublicationStateMachine(vcs, "owner")

    await machine.publish(origin, "message", "a" * 64)

    vcs.reopen_thread.assert_not_awaited()
    vcs.create_continuation_thread.assert_awaited_once()
    vcs.resolve_thread.assert_awaited_once_with("continuation")


@pytest.mark.asyncio
async def test_existing_publication_skips_post_and_only_resolves_continuation():
    """Catches duplicate LLM-visible publication after a prior POST succeeded."""
    marker = ReviewMarker(
        version="v2", kind=MarkerKind.FOLLOWUP, head="a" * 40,
        covered=("reply",), verdict="open", origin="origin", publication="b" * 64,
    )
    origin = item("origin", resolved=True)
    continuation = item(
        "continuation",
        resolved=False,
        body=decorate_ai_message("answer", marker),
    )
    vcs = SimpleNamespace(
        can_reply_resolved=False, can_reopen=False,
        create_inline_reply=AsyncMock(), create_continuation_thread=AsyncMock(),
        resolve_thread=AsyncMock(),
        get_inline_threads=AsyncMock(return_value=[origin]),
        get_general_threads=AsyncMock(return_value=[continuation]),
    )
    machine = FollowupPublicationStateMachine(vcs, "owner")

    result = await machine.find_publication("b" * 64)

    assert result.id == "continuation"
    await machine.publish(origin, "ignored", "b" * 64)
    vcs.create_inline_reply.assert_not_awaited()
    vcs.create_continuation_thread.assert_not_awaited()
    vcs.resolve_thread.assert_awaited_once_with("continuation")


@pytest.mark.asyncio
async def test_provider_can_reply_to_closed_origin_without_state_change():
    """Catches reopening or creating a continuation despite direct-reply capability."""
    origin = item("origin", resolved=True)
    vcs = SimpleNamespace(
        can_reply_resolved=True, can_reopen=True,
        create_inline_reply=AsyncMock(), reopen_thread=AsyncMock(),
        create_continuation_thread=AsyncMock(), resolve_thread=AsyncMock(),
        get_inline_threads=AsyncMock(return_value=[origin]), get_general_threads=AsyncMock(return_value=[]),
    )

    await FollowupPublicationStateMachine(vcs, "owner").publish(
        origin, "message", "a" * 64
    )

    vcs.create_inline_reply.assert_awaited_once_with("origin", "message")
    vcs.reopen_thread.assert_not_awaited()
    vcs.create_continuation_thread.assert_not_awaited()
    vcs.resolve_thread.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved,can_reply", [(False, False), (True, True)])
async def test_ambiguous_direct_post_recovers_when_publication_is_found(resolved, can_reply):
    """Catches missing discovery for both ordinary-open and direct-closed POSTs."""
    origin = item("origin", resolved=resolved)
    marker = ReviewMarker(
        version="v2", kind=MarkerKind.FOLLOWUP, head="a" * 40,
        covered=("reply",), verdict="open", origin="origin", publication="e" * 64,
    )
    published = item("origin", resolved=resolved, body=decorate_ai_message("answer", marker))
    vcs = SimpleNamespace(
        can_reply_resolved=can_reply, can_reopen=False,
        create_inline_reply=AsyncMock(side_effect=TimeoutError("ambiguous")),
        resolve_thread=AsyncMock(),
        get_inline_threads=AsyncMock(side_effect=[[origin], [published]]),
        get_general_threads=AsyncMock(return_value=[]),
    )

    result = await FollowupPublicationStateMachine(vcs, "owner").publish(
        origin, "message", "e" * 64
    )

    assert result.thread.id == "origin"
    vcs.create_inline_reply.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved,can_reply", [(False, False), (True, True)])
async def test_ambiguous_direct_post_raises_when_publication_is_not_found(
    resolved, can_reply
):
    origin = item("origin", resolved=resolved)
    vcs = SimpleNamespace(
        can_reply_resolved=can_reply, can_reopen=False,
        create_inline_reply=AsyncMock(side_effect=TimeoutError("ambiguous")),
        get_inline_threads=AsyncMock(return_value=[origin]),
        get_general_threads=AsyncMock(return_value=[]),
    )

    with pytest.raises(TimeoutError):
        await FollowupPublicationStateMachine(vcs, "owner").publish(
            origin, "message", "f" * 64
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("found", [True, False])
async def test_ambiguous_reopen_post_discovers_then_restores(found):
    origin = item("origin", resolved=True)
    reopened = item("origin", resolved=False)
    marker = ReviewMarker(
        version="v2", kind=MarkerKind.FOLLOWUP, head="a" * 40,
        covered=("reply",), verdict="clarify", origin="origin", publication="7" * 64,
    )
    published = item(
        "origin", resolved=False,
        body=decorate_ai_message("answer", marker) + f"\n\n{RESTORE_RESOLVED_MARKER}",
    )
    vcs = SimpleNamespace(
        can_reply_resolved=False, can_reopen=True,
        reopen_thread=AsyncMock(), resolve_thread=AsyncMock(),
        create_inline_reply=AsyncMock(side_effect=TimeoutError("ambiguous")),
        get_inline_threads=AsyncMock(side_effect=[[origin], [reopened], [published if found else reopened]]),
        get_general_threads=AsyncMock(return_value=[]),
    )
    machine = FollowupPublicationStateMachine(vcs, "owner")

    if found:
        result = await machine.publish(origin, "message", "7" * 64)
        assert result.thread.id == "origin"
    else:
        with pytest.raises(TimeoutError):
            await machine.publish(origin, "message", "7" * 64)
    vcs.resolve_thread.assert_awaited_once_with("origin")


@pytest.mark.asyncio
async def test_failed_reopen_verification_still_restores_original_state():
    origin = item("origin", resolved=True)
    vcs = SimpleNamespace(
        can_reply_resolved=False, can_reopen=True,
        reopen_thread=AsyncMock(), resolve_thread=AsyncMock(), create_inline_reply=AsyncMock(),
        get_inline_threads=AsyncMock(side_effect=[[origin], [origin]]),
        get_general_threads=AsyncMock(return_value=[]),
    )

    with pytest.raises(RuntimeError, match="did not reopen"):
        await FollowupPublicationStateMachine(vcs, "owner").publish(
            origin, "message", "8" * 64
        )

    vcs.create_inline_reply.assert_not_awaited()
    vcs.resolve_thread.assert_awaited_once_with("origin")


@pytest.mark.asyncio
async def test_successful_continuation_requires_discovery_confirmation():
    origin = item("origin", resolved=True)
    returned = item("returned", resolved=False)
    vcs = SimpleNamespace(
        can_reply_resolved=False, can_reopen=False,
        create_continuation_thread=AsyncMock(return_value=returned),
        resolve_thread=AsyncMock(),
        get_inline_threads=AsyncMock(return_value=[origin]),
        get_general_threads=AsyncMock(return_value=[]),
    )

    with pytest.raises(RuntimeError, match="not found"):
        await FollowupPublicationStateMachine(vcs, "owner").publish(
            origin, "message", "1" * 64
        )

    vcs.resolve_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_continuation_post_is_recovered_by_publication_key():
    """Catches retrying POST when the provider stored a response before an error."""
    origin = item("origin", resolved=True)
    marker = ReviewMarker(
        version="v2", kind=MarkerKind.FOLLOWUP, head="a" * 40,
        covered=("reply",), verdict="open", origin="origin", publication="d" * 64,
    )
    continuation = item("continuation", resolved=False, body=decorate_ai_message("answer", marker))
    vcs = SimpleNamespace(
        can_reply_resolved=False, can_reopen=False,
        create_continuation_thread=AsyncMock(side_effect=TimeoutError("ambiguous")),
        resolve_thread=AsyncMock(),
        get_inline_threads=AsyncMock(side_effect=[[origin], [origin]]),
        get_general_threads=AsyncMock(side_effect=[[], [continuation]]),
    )

    result = await FollowupPublicationStateMachine(vcs, "owner").publish(
        origin, "message", "d" * 64
    )

    assert result.thread.id == "continuation"
    vcs.create_continuation_thread.assert_awaited_once()
    vcs.resolve_thread.assert_awaited_once_with("continuation")
