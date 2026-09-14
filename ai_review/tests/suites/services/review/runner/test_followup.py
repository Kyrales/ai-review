from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_review.config import settings
from ai_review.services.review.runner.followup import FollowupReviewRunner
from ai_review.services.review.runner.followup_publication import RESTORE_RESOLVED_MARKER
from ai_review.libs.config.knowledge import KnowledgeConfig, TrustedReviewersConfig
from ai_review.services.knowledge.block import render_knowledge_block
from ai_review.services.knowledge.schema import KnowledgeCandidate
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


def knowledge_candidate(reply_id: str) -> KnowledgeCandidate:
    return KnowledgeCandidate.model_validate(
        {
            "source_reply_id": reply_id,
            "source_quote": "Запомни это правило",
            "type": "new_check",
            "rule": "Проверять это правило.",
            "rationale": "Указание главного ревьювера.",
        }
    )


@pytest.mark.asyncio
async def test_trusted_pending_reply_is_extracted_after_verdict(monkeypatch):
    """Catches selecting untrusted/all discussion replies or extracting before verdict."""
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    monkeypatch.setattr(
        settings,
        "knowledge",
        KnowledgeConfig(
            enabled=True,
            trusted_reviewers=TrustedReviewersConfig(gitflic=["Lead"]),
            max_rules_per_reply=3,
        ),
    )
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message("finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)),
        author=UserSchema(id="owner"),
    )
    trusted = ReviewCommentSchema(
        id="trusted-1",
        parent_id="thread",
        author=UserSchema(id="lead-id", username="lead"),
        body="Запомни это правило",
    )
    untrusted = ReviewCommentSchema(
        id="other-1",
        parent_id="thread",
        author=UserSchema(id="other-id", username="other"),
        body="Не использовать как знание",
    )
    item = thread([finding, untrusted, trusted])
    events: list[str] = []
    gateway = SimpleNamespace(
        ask=AsyncMock(
            side_effect=lambda *_: events.append("verdict")
            or (
                '{"verdict":"withdrawn","message":"Проверено","silent":true,'
                '"silent_source_reply_id":"trusted-1"}'
            )
        )
    )
    extractor = SimpleNamespace(
        extract=AsyncMock(
            side_effect=lambda *_args, **_kwargs: events.append("knowledge")
            or (knowledge_candidate("trusted-1"),)
        )
    )
    vcs = SimpleNamespace(
        provider="GITFLIC",
        project_key="owner/project",
        merge_request_id=1,
        get_inline_threads=AsyncMock(return_value=[item]),
        get_general_threads=AsyncMock(return_value=[]),
        create_inline_reply=AsyncMock(),
        resolve_thread=AsyncMock(),
    )

    await FollowupReviewRunner(vcs, gateway, knowledge_extractor=extractor).run()

    assert events == ["verdict", "knowledge"]
    sources = extractor.extract.await_args.args[1]
    assert [(source.reply_id, source.username) for source in sources] == [("trusted-1", "lead")]
    assert "#ai-review-knowledge" in vcs.create_inline_reply.await_args.args[1]
    vcs.resolve_thread.assert_awaited_once_with("thread")


@pytest.mark.asyncio
async def test_disabled_knowledge_does_not_call_extractor(monkeypatch):
    """Catches an extra knowledge LLM call when the feature is disabled."""
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    monkeypatch.setattr(settings, "knowledge", KnowledgeConfig(enabled=False))
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message("finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)),
        author=UserSchema(id="owner"),
    )
    reply = ReviewCommentSchema(
        id="reply", parent_id="thread", author=UserSchema(username="lead"), body="Запомни"
    )
    item = thread([finding, reply])
    vcs = SimpleNamespace(
        get_inline_threads=AsyncMock(return_value=[item]),
        create_inline_reply=AsyncMock(),
        resolve_thread=AsyncMock(),
    )
    gateway = SimpleNamespace(ask=AsyncMock(return_value='{"verdict":"open","message":"ok"}'))
    extractor = SimpleNamespace(extract=AsyncMock())

    await FollowupReviewRunner(vcs, gateway, knowledge_extractor=extractor).run()

    extractor.extract.assert_not_awaited()


@pytest.mark.asyncio
async def test_withdrawn_is_published_and_resolved(monkeypatch):
    """Catches treating withdrawn as a non-terminal verdict."""
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message("finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)),
        author=UserSchema(id="owner"),
    )
    reply = ReviewCommentSchema(id="reply", parent_id="thread", body="Это ложное замечание")
    before = thread([finding, reply])
    after = thread(
        [
            finding,
            reply,
            ReviewCommentSchema(
                id="ai",
                parent_id="thread",
                author=UserSchema(id="owner"),
                body=decorate_ai_message(
                    "Снимаю",
                    ReviewMarker(kind=MarkerKind.FOLLOWUP, head=HEAD, covered=("reply",), verdict="withdrawn", version="v2", origin="thread", publication="b" * 64),
                ),
            ),
        ]
    )
    vcs = SimpleNamespace(
        provider="GITFLIC", project_key="owner/project", merge_request_id=1,
        get_inline_threads=AsyncMock(side_effect=[[before], [after], [after]]),
        get_general_threads=AsyncMock(return_value=[]),
        create_inline_reply=AsyncMock(), resolve_thread=AsyncMock(),
    )
    gateway = SimpleNamespace(ask=AsyncMock(return_value='{"verdict":"withdrawn","message":"Снимаю"}'))

    await FollowupReviewRunner(vcs, gateway).run()

    assert "verdict=withdrawn" in vcs.create_inline_reply.await_args.args[1]
    vcs.resolve_thread.assert_awaited_once_with("thread")


@pytest.mark.asyncio
async def test_continuation_marker_covers_origin_reply_without_duplicate_llm(monkeypatch):
    """Catches ignoring covered IDs published in a continuation thread."""
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message("finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)),
        author=UserSchema(id="owner"),
    )
    reply = ReviewCommentSchema(id="opaque-reply", parent_id="thread", body="Ответ")
    origin = ReviewThreadSchema(
        id="thread", kind=ThreadKind.INLINE, comments=[finding, reply], resolved=True
    )
    marker = ReviewMarker(
        version="v2", kind=MarkerKind.FOLLOWUP, head=HEAD,
        covered=("opaque-reply",), verdict="open", origin="thread", publication="c" * 64,
    )
    continuation = ReviewThreadSchema(
        id="continuation", kind=ThreadKind.SUMMARY, resolved=False,
        comments=[ReviewCommentSchema(
            id="ai", body=decorate_ai_message("Ответ", marker), author=UserSchema(id="owner")
        )],
    )
    vcs = SimpleNamespace(
        get_inline_threads=AsyncMock(return_value=[origin]),
        get_general_threads=AsyncMock(return_value=[continuation]),
        create_inline_reply=AsyncMock(), resolve_thread=AsyncMock(),
    )
    gateway = SimpleNamespace(ask=AsyncMock())

    await FollowupReviewRunner(vcs, gateway).run()

    gateway.ask.assert_not_awaited()
    vcs.create_inline_reply.assert_not_awaited()
    vcs.resolve_thread.assert_awaited_once_with("continuation")


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["open", "clarify"])
async def test_reopen_resolve_failure_retries_only_resolve(monkeypatch, verdict):
    """Catches losing the restore obligation for a non-terminal publication."""
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message("finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)),
        author=UserSchema(id="owner"),
    )
    reply = ReviewCommentSchema(id="reply", parent_id="thread", body="Ответ")
    marker = ReviewMarker(
        version="v2", kind=MarkerKind.FOLLOWUP, head=HEAD,
        covered=("reply",), verdict=verdict, origin="thread", publication="9" * 64,
    )
    ai_reply = ReviewCommentSchema(
        id="ai", parent_id="thread", author=UserSchema(id="owner"),
        body=decorate_ai_message("Ответ", marker) + f"\n\n{RESTORE_RESOLVED_MARKER}",
    )
    origin = ReviewThreadSchema(
        id="thread", kind=ThreadKind.INLINE,
        comments=[finding, reply, ai_reply], resolved=False,
    )
    vcs = SimpleNamespace(
        get_inline_threads=AsyncMock(return_value=[origin]),
        get_general_threads=AsyncMock(return_value=[]),
        create_inline_reply=AsyncMock(), resolve_thread=AsyncMock(),
    )
    gateway = SimpleNamespace(ask=AsyncMock())

    await FollowupReviewRunner(vcs, gateway).run()

    gateway.ask.assert_not_awaited()
    vcs.create_inline_reply.assert_not_awaited()
    vcs.resolve_thread.assert_awaited_once_with("thread")


@pytest.mark.asyncio
async def test_terminal_gitflic_reply_recloses_origin_thread(monkeypatch):
    """GitFlic opens a resolved discussion when a reply is added, so close it again."""
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message("finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)),
        author=UserSchema(id="owner"),
    )
    reply = ReviewCommentSchema(id="reply", parent_id="thread", body="Исправил")
    origin = ReviewThreadSchema(
        id="thread", kind=ThreadKind.INLINE, comments=[finding, reply], resolved=True
    )
    vcs = SimpleNamespace(
        provider="GITFLIC", project_key="owner/project", merge_request_id=1,
        can_reply_resolved=True, reply_reopens_resolved=True, can_reopen=False,
        get_inline_threads=AsyncMock(return_value=[origin]), get_general_threads=AsyncMock(return_value=[]),
        create_inline_reply=AsyncMock(), resolve_thread=AsyncMock(),
    )
    gateway = SimpleNamespace(ask=AsyncMock(return_value='{"verdict":"fixed","message":"ok"}'))

    await FollowupReviewRunner(vcs, gateway).run()

    vcs.create_inline_reply.assert_awaited_once()
    vcs.resolve_thread.assert_awaited_once_with("thread")


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
        get_inline_threads=AsyncMock(side_effect=[[first], [first], [second]]),
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
async def test_pending_knowledge_resolve_retries_without_llm(monkeypatch):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message(
            "finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)
        ),
        author=UserSchema(id="owner"),
    )
    followup = ReviewCommentSchema(
        id="ai",
        parent_id="thread",
        author=UserSchema(id="owner"),
        body=decorate_ai_message(
            "Проверено\n\n"
            + render_knowledge_block((knowledge_candidate("human"),)),
            ReviewMarker(
                version="v2",
                kind=MarkerKind.FOLLOWUP,
                head=HEAD,
                covered=("human",),
                verdict="open",
                origin="thread",
                publication="c" * 64,
            ),
        ),
    )
    item = thread([finding, followup])
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
async def test_bare_knowledge_tag_does_not_trigger_resolve_retry(monkeypatch):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    finding = ReviewCommentSchema(
        id="root",
        author=UserSchema(id="owner"),
        body=decorate_ai_message(
            "finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)
        ),
    )
    followup = ReviewCommentSchema(
        id="ai",
        author=UserSchema(id="owner"),
        body=decorate_ai_message(
            "Повреждено\n\n#ai-review-knowledge",
            ReviewMarker(
                version="v2",
                kind=MarkerKind.FOLLOWUP,
                head=HEAD,
                covered=("human",),
                verdict="open",
                origin="thread",
                publication="d" * 64,
            ),
        ),
    )
    item = thread([finding, followup])
    vcs = SimpleNamespace(
        get_inline_threads=AsyncMock(return_value=[item]),
        resolve_thread=AsyncMock(),
    )

    await FollowupReviewRunner(vcs, SimpleNamespace(ask=AsyncMock())).run()

    vcs.resolve_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_already_resolved_followup_is_not_resolved_again(monkeypatch):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    finding = ReviewCommentSchema(
        id="root",
        author=UserSchema(id="owner"),
        body=decorate_ai_message(
            "finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)
        ),
    )
    followup = ReviewCommentSchema(
        id="ai",
        author=UserSchema(id="owner"),
        body=decorate_ai_message(
            "Исправлено",
            ReviewMarker(kind=MarkerKind.FOLLOWUP, head=HEAD, verdict="fixed"),
        ),
    )
    item = thread([finding, followup])
    item.resolved = True
    vcs = SimpleNamespace(
        get_inline_threads=AsyncMock(return_value=[item]),
        resolve_thread=AsyncMock(),
    )

    await FollowupReviewRunner(vcs, SimpleNamespace(ask=AsyncMock())).run()

    vcs.resolve_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_v1_followup_keeps_covered_reply_out_of_pending(monkeypatch):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    reply_id = "12345678-1234-1234-1234-123456789abc"
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message(
            "finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)
        ),
        author=UserSchema(id="owner"),
    )
    human_reply = ReviewCommentSchema(
        id=reply_id,
        parent_id="thread",
        author=UserSchema(id="developer"),
        body="Исправил",
    )
    v1_followup = ReviewCommentSchema(
        id="ai-followup",
        parent_id="thread",
        author=UserSchema(id="owner"),
        body=decorate_ai_message(
            "Проверено",
            ReviewMarker(
                kind=MarkerKind.FOLLOWUP,
                head=HEAD,
                covered=(reply_id,),
                verdict="open",
            ),
        ),
    )
    item = thread([finding, human_reply, v1_followup])
    vcs = SimpleNamespace(
        get_inline_threads=AsyncMock(return_value=[item]),
        create_inline_reply=AsyncMock(),
        resolve_thread=AsyncMock(),
    )
    gateway = SimpleNamespace(ask=AsyncMock())

    await FollowupReviewRunner(vcs, gateway).run()

    gateway.ask.assert_not_awaited()
    vcs.create_inline_reply.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_ignored_template_fast_path_still_extracts_trusted_knowledge(monkeypatch):
    """Catches returning from the role-template fast path before extraction."""
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    monkeypatch.setattr(settings.review, "ignore_1c_role_restriction_templates", ["ПоЗначениям"])
    monkeypatch.setattr(
        settings,
        "knowledge",
        KnowledgeConfig(
            enabled=True,
            trusted_reviewers=TrustedReviewersConfig(gitflic=["lead"]),
        ),
    )
    finding = ReviewCommentSchema(
        id="root", file="Roles/Test/Rights.rights", line=3,
        body=decorate_ai_message("finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)),
        author=UserSchema(id="owner"),
    )
    reply = ReviewCommentSchema(
        id="reply", parent_id="thread", author=UserSchema(username="lead"),
        body="Запомни это правило",
    )
    item = ReviewThreadSchema(
        id="thread", kind=ThreadKind.INLINE, file="Roles/Test/Rights.rights", line=3,
        comments=[finding, reply], resolved=False,
    )
    vcs = SimpleNamespace(
        provider="GITFLIC", project_key="owner/project", merge_request_id=1,
        get_inline_threads=AsyncMock(return_value=[item]), get_general_threads=AsyncMock(return_value=[]),
        get_review_info=AsyncMock(return_value=SimpleNamespace(base_sha="b" * 40, head_sha=HEAD)),
        create_inline_reply=AsyncMock(), resolve_thread=AsyncMock(),
    )
    source = """<rights>\n<restrictionTemplate>\n<name>ПоЗначениям</name>\nstandard\n</restrictionTemplate>\n</rights>"""
    git = SimpleNamespace(
        get_diff_for_file=lambda *_args, **_kwargs: "",
        get_file_at_commit=lambda *_args, **_kwargs: source,
    )
    verdict_gateway = SimpleNamespace(ask=AsyncMock())
    extractor = SimpleNamespace(extract=AsyncMock(return_value=(knowledge_candidate("reply"),)))

    await FollowupReviewRunner(vcs, verdict_gateway, git=git, knowledge_extractor=extractor).run()

    verdict_gateway.ask.assert_not_awaited()
    extractor.extract.assert_awaited_once()
    assert "#ai-review-knowledge" in vcs.create_inline_reply.await_args.args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved", [False, True])
async def test_trusted_reviewer_withdrawal_is_silent_and_closes(monkeypatch, resolved):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    monkeypatch.setattr(
        settings,
        "knowledge",
        KnowledgeConfig(
            enabled=True,
            trusted_reviewers=TrustedReviewersConfig(gitflic=["Lead"]),
        ),
    )
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message(
            "finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)
        ),
        author=UserSchema(id="owner", username="lead"),
    )
    decision = ReviewCommentSchema(
        id="decision",
        parent_id="thread",
        author=UserSchema(id="lead-id", username="lead"),
        body="Это ложное срабатывание, замечание следует снять.",
    )
    item = ReviewThreadSchema(
        id="thread",
        kind=ThreadKind.INLINE,
        comments=[finding, decision],
        resolved=resolved,
    )
    gateway = SimpleNamespace(
        ask=AsyncMock(
            return_value=(
                '{"verdict":"withdrawn","message":"Замечание снято","silent":true,'
                '"silent_source_reply_id":"decision"}'
            )
        )
    )
    extractor = SimpleNamespace(extract=AsyncMock(return_value=()))
    vcs = SimpleNamespace(
        provider="GITFLIC",
        project_key="owner/project",
        merge_request_id=1,
        get_inline_threads=(
            AsyncMock(return_value=[item])
            if resolved
            else AsyncMock(side_effect=[[item], [item]])
        ),
        get_general_threads=AsyncMock(return_value=[]),
        create_inline_reply=AsyncMock(),
        resolve_thread=AsyncMock(),
    )

    await FollowupReviewRunner(vcs, gateway, knowledge_extractor=extractor).run()

    prompt, system_prompt = gateway.ask.await_args.args
    assert "последний новый ответ главного ревьювера: decision" in prompt
    assert "только помеченный последний ответ" in system_prompt
    vcs.create_inline_reply.assert_not_awaited()
    if resolved:
        vcs.resolve_thread.assert_not_awaited()
    else:
        vcs.resolve_thread.assert_awaited_once_with("thread")


@pytest.mark.asyncio
async def test_untrusted_reviewer_cannot_silently_withdraw(monkeypatch):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    monkeypatch.setattr(
        settings,
        "knowledge",
        KnowledgeConfig(
            enabled=True,
            trusted_reviewers=TrustedReviewersConfig(gitflic=["Lead"]),
        ),
    )
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message(
            "finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)
        ),
        author=UserSchema(id="owner"),
    )
    reply = ReviewCommentSchema(
        id="reply",
        parent_id="thread",
        author=UserSchema(id="developer", username="developer"),
        body="Это не ошибка.",
    )
    item = thread([finding, reply])
    vcs = SimpleNamespace(
        provider="GITFLIC",
        project_key="owner/project",
        merge_request_id=1,
        get_inline_threads=AsyncMock(return_value=[item]),
        get_general_threads=AsyncMock(return_value=[]),
        create_inline_reply=AsyncMock(),
        resolve_thread=AsyncMock(),
    )
    gateway = SimpleNamespace(
        ask=AsyncMock(
            return_value=(
                '{"verdict":"withdrawn","message":"Замечание снято","silent":true,'
                '"silent_source_reply_id":"reply"}'
            )
        )
    )

    await FollowupReviewRunner(vcs, gateway).run()

    vcs.create_inline_reply.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_trusted_decision_cannot_silently_withdraw(monkeypatch):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    monkeypatch.setattr(
        settings,
        "knowledge",
        KnowledgeConfig(
            enabled=True,
            trusted_reviewers=TrustedReviewersConfig(gitflic=["Lead"]),
        ),
    )
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message(
            "finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)
        ),
        author=UserSchema(id="owner"),
    )
    old_decision = ReviewCommentSchema(
        id="old-decision",
        parent_id="thread",
        author=UserSchema(id="lead-id", username="lead"),
        body="Замечание следует снять.",
    )
    latest_question = ReviewCommentSchema(
        id="question",
        parent_id="thread",
        author=UserSchema(id="lead-id", username="lead"),
        body="А это точно работает для управляемой формы?",
    )
    item = thread([finding, old_decision, latest_question])
    vcs = SimpleNamespace(
        provider="GITFLIC",
        project_key="owner/project",
        merge_request_id=1,
        get_inline_threads=AsyncMock(return_value=[item]),
        get_general_threads=AsyncMock(return_value=[]),
        create_inline_reply=AsyncMock(),
        resolve_thread=AsyncMock(),
    )
    gateway = SimpleNamespace(
        ask=AsyncMock(
            return_value=(
                '{"verdict":"withdrawn","message":"Замечание снято","silent":true,'
                '"silent_source_reply_id":"old-decision"}'
            )
        )
    )

    await FollowupReviewRunner(vcs, gateway).run()

    vcs.create_inline_reply.assert_awaited_once()
    vcs.resolve_thread.assert_awaited_once_with("thread")


@pytest.mark.asyncio
async def test_silent_withdrawal_does_not_close_after_new_reply(monkeypatch):
    monkeypatch.setenv("AI_REVIEW_GITFLIC_USER_ID", "owner")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", HEAD)
    monkeypatch.setattr(
        settings,
        "knowledge",
        KnowledgeConfig(
            enabled=True,
            trusted_reviewers=TrustedReviewersConfig(gitflic=["Lead"]),
        ),
    )
    finding = ReviewCommentSchema(
        id="root",
        body=decorate_ai_message(
            "finding", ReviewMarker(kind=MarkerKind.FINDING, head=HEAD)
        ),
        author=UserSchema(id="owner"),
    )
    decision = ReviewCommentSchema(
        id="decision",
        parent_id="thread",
        author=UserSchema(id="lead-id", username="lead"),
        body="Это ложное срабатывание.",
    )
    new_reply = ReviewCommentSchema(
        id="11111111-1111-4111-8111-111111111111",
        parent_id="thread",
        author=UserSchema(id="developer", username="developer"),
        body="Добавлю ещё контекст.",
    )
    parallel_followup = ReviewCommentSchema(
        id="parallel-followup",
        parent_id="thread",
        author=UserSchema(id="owner"),
        body=decorate_ai_message(
            "Ответ уже обработан параллельным запуском.",
            ReviewMarker(
                kind=MarkerKind.FOLLOWUP,
                head=HEAD,
                covered=("11111111-1111-4111-8111-111111111111",),
                verdict="open",
            ),
        ),
    )
    original = thread([finding, decision])
    refreshed = thread([finding, decision, new_reply, parallel_followup])
    vcs = SimpleNamespace(
        provider="GITFLIC",
        project_key="owner/project",
        merge_request_id=1,
        get_inline_threads=AsyncMock(side_effect=[[original], [refreshed]]),
        get_general_threads=AsyncMock(return_value=[]),
        create_inline_reply=AsyncMock(),
        resolve_thread=AsyncMock(),
    )
    gateway = SimpleNamespace(
        ask=AsyncMock(
            return_value=(
                '{"verdict":"withdrawn","message":"Замечание снято","silent":true,'
                '"silent_source_reply_id":"decision"}'
            )
        )
    )

    await FollowupReviewRunner(vcs, gateway).run()

    vcs.create_inline_reply.assert_not_awaited()
    vcs.resolve_thread.assert_not_awaited()
