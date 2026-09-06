from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_review.services.review.runner.followup import FollowupReviewRunner
from ai_review.services.vcs.gitflic.markers import MarkerKind, ReviewMarker, decorate_ai_message
from ai_review.services.vcs.types import ReviewCommentSchema, ReviewThreadSchema, ThreadKind, UserSchema


HEAD = "a" * 40


def thread(comments):
    return ReviewThreadSchema(id="thread", kind=ThreadKind.INLINE, comments=comments)


@pytest.mark.asyncio
async def test_fixed_does_not_resolve_when_new_reply_arrives(monkeypatch):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    finding = ReviewCommentSchema(
        id="root", body=decorate_ai_message("finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)),
        author=UserSchema(id="owner"), thread_id="thread",
    )
    first = thread([finding, ReviewCommentSchema(id="12345678-1234-1234-1234-123456789abc", body="fixed", parent_id="thread")])
    second = thread([*first.comments, ReviewCommentSchema(id="12345678-1234-1234-1234-123456789abd", body="also fixed", parent_id="thread")])
    vcs = SimpleNamespace(
        get_inline_threads=AsyncMock(side_effect=[[first], [second]]),
        create_inline_reply=AsyncMock(), resolve_thread=AsyncMock(),
    )
    gateway = SimpleNamespace(ask=AsyncMock(return_value='{"verdict":"fixed","message":"ok"}'))

    await FollowupReviewRunner(vcs, gateway).run()

    vcs.resolve_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_resolve_retries_without_llm(monkeypatch):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    finding = ReviewCommentSchema(id="root", body=decorate_ai_message("finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)), author=UserSchema(id="owner"))
    reply = ReviewCommentSchema(id="ai", parent_id="thread", author=UserSchema(id="owner"), body=decorate_ai_message("fixed", ReviewMarker(kind=MarkerKind.FOLLOWUP, head=HEAD, verdict="fixed")))
    item = thread([finding, reply])
    vcs = SimpleNamespace(get_inline_threads=AsyncMock(return_value=[item]), create_inline_reply=AsyncMock(), resolve_thread=AsyncMock())
    gateway = SimpleNamespace(ask=AsyncMock())

    await FollowupReviewRunner(vcs, gateway).run()

    vcs.resolve_thread.assert_awaited_once_with("thread")
    gateway.ask.assert_not_awaited()
