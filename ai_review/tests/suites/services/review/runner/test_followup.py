from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_review.config import settings
from ai_review.services.review.runner.followup import FollowupReviewRunner
from ai_review.services.vcs.gitflic.markers import (
    MarkerKind,
    ReviewMarker,
    decorate_ai_message,
)
from ai_review.services.vcs.types import (
    ReviewCommentSchema,
    ReviewThreadSchema,
    ThreadKind,
    UserSchema,
)


HEAD = "a" * 40


def thread(comments):
    return ReviewThreadSchema(id="thread", kind=ThreadKind.INLINE, comments=comments)


@pytest.mark.asyncio
async def test_fixed_does_not_resolve_when_new_reply_arrives(monkeypatch):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message(
            "finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)
        ),
        author=UserSchema(id="owner"),
        thread_id="thread",
    )
    first = thread(
        [
            finding,
            ReviewCommentSchema(
                id="12345678-1234-1234-1234-123456789abc",
                body="fixed",
                parent_id="thread",
            ),
        ]
    )
    second = thread(
        [
            *first.comments,
            ReviewCommentSchema(
                id="12345678-1234-1234-1234-123456789abd",
                body="also fixed",
                parent_id="thread",
            ),
        ]
    )
    vcs = SimpleNamespace(
        get_inline_threads=AsyncMock(side_effect=[[first], [second]]),
        create_inline_reply=AsyncMock(),
        resolve_thread=AsyncMock(),
    )
    gateway = SimpleNamespace(
        ask=AsyncMock(return_value='{"verdict":"fixed","message":"ok"}')
    )

    await FollowupReviewRunner(vcs, gateway).run()

    vcs.resolve_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_pending_resolve_retries_without_llm(monkeypatch):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message(
            "finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)
        ),
        author=UserSchema(id="owner"),
    )
    reply = ReviewCommentSchema(
        id="ai",
        parent_id="thread",
        author=UserSchema(id="owner"),
        body=decorate_ai_message(
            "fixed", ReviewMarker(kind=MarkerKind.FOLLOWUP, head=HEAD, verdict="fixed")
        ),
    )
    item = thread([finding, reply])
    vcs = SimpleNamespace(
        get_inline_threads=AsyncMock(return_value=[item]),
        create_inline_reply=AsyncMock(),
        resolve_thread=AsyncMock(),
    )
    gateway = SimpleNamespace(ask=AsyncMock())

    await FollowupReviewRunner(vcs, gateway).run()

    vcs.resolve_thread.assert_awaited_once_with("thread")
    gateway.ask.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmarked_reply_from_token_owner_is_treated_as_human(monkeypatch):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message(
            "finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)
        ),
        author=UserSchema(id="owner"),
    )
    manual_reply = ReviewCommentSchema(
        id="12345678-1234-1234-1234-123456789abc",
        parent_id="thread",
        author=UserSchema(id="owner"),
        body="Исправил вручную",
    )
    item = thread([finding, manual_reply])
    vcs = SimpleNamespace(
        get_inline_threads=AsyncMock(side_effect=[[item], [item]]),
        create_inline_reply=AsyncMock(),
        resolve_thread=AsyncMock(),
    )
    gateway = SimpleNamespace(
        ask=AsyncMock(
            return_value='{"verdict":"open","message":"Нужно проверить ещё раз"}'
        )
    )

    await FollowupReviewRunner(vcs, gateway).run()

    vcs.create_inline_reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_followup_prompt_contains_current_code_and_diff(monkeypatch):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    finding = ReviewCommentSchema(
        id="root",
        file="module.py",
        line=2,
        body=decorate_ai_message(
            "finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)
        ),
        author=UserSchema(id="owner"),
    )
    reply = ReviewCommentSchema(
        id="12345678-1234-1234-1234-123456789abc",
        parent_id="thread",
        author=UserSchema(id="developer"),
        body="Исправил",
    )
    item = ReviewThreadSchema(
        id="thread",
        kind=ThreadKind.INLINE,
        file="module.py",
        line=2,
        comments=[finding, reply],
    )
    vcs = SimpleNamespace(
        get_inline_threads=AsyncMock(side_effect=[[item], [item]]),
        get_review_info=AsyncMock(
            return_value=SimpleNamespace(base_sha="b" * 40, head_sha=HEAD)
        ),
        create_inline_reply=AsyncMock(),
        resolve_thread=AsyncMock(),
    )
    git = SimpleNamespace(
        get_diff_for_file=lambda *_args, **_kwargs: "UNIQUE_DIFF",
        get_file_at_commit=lambda *_args, **_kwargs: (
            "line 1\nUNIQUE_CURRENT_CODE\nline 3"
        ),
    )
    gateway = SimpleNamespace(
        ask=AsyncMock(return_value='{"verdict":"open","message":"Проверено"}')
    )

    await FollowupReviewRunner(vcs, gateway, git=git).run()

    prompt = gateway.ask.await_args.args[0]
    assert "UNIQUE_DIFF" in prompt
    assert "UNIQUE_CURRENT_CODE" in prompt


@pytest.mark.asyncio
async def test_followup_skips_finding_inside_ignored_role_template(monkeypatch):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    monkeypatch.setattr(
        settings.review, "ignore_1c_role_restriction_templates", ["ПоЗначениям"]
    )
    finding = ReviewCommentSchema(
        id="root",
        file="Roles/Test/Rights.rights",
        line=3,
        body=decorate_ai_message(
            "finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)
        ),
        author=UserSchema(id="owner"),
    )
    reply = ReviewCommentSchema(
        id="12345678-1234-1234-1234-123456789abc",
        parent_id="thread",
        author=UserSchema(id="developer"),
        body="Исправил",
    )
    item = ReviewThreadSchema(
        id="thread",
        kind=ThreadKind.INLINE,
        file="Roles/Test/Rights.rights",
        line=3,
        comments=[finding, reply],
    )
    vcs = SimpleNamespace(
        get_inline_threads=AsyncMock(return_value=[item]),
        get_review_info=AsyncMock(
            return_value=SimpleNamespace(base_sha="b" * 40, head_sha=HEAD)
        ),
        create_inline_reply=AsyncMock(),
        resolve_thread=AsyncMock(),
    )
    source = """<rights>
<restrictionTemplate>
<name>ПоЗначениям</name>
standard
</restrictionTemplate>
</rights>"""
    git = SimpleNamespace(
        get_diff_for_file=lambda *_args, **_kwargs: "",
        get_file_at_commit=lambda *_args, **_kwargs: source,
    )
    gateway = SimpleNamespace(ask=AsyncMock())

    await FollowupReviewRunner(vcs, gateway, git=git).run()

    gateway.ask.assert_not_awaited()
    vcs.create_inline_reply.assert_awaited_once()
    vcs.resolve_thread.assert_awaited_once_with("thread")
