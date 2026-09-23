import pytest
from unittest.mock import AsyncMock

from ai_review.clients.gitflic.schema import (
    GitFlicAuthor,
    GitFlicBranch,
    GitFlicBranchProtection,
    GitFlicMergeRequest,
    GitFlicPipelineStartResponse,
    GitFlicStatus,
)
from ai_review.services.dispatch.models import (
    DispatchMode,
    DispatchSelection,
    classify_dispatch,
)
from ai_review.services.dispatch.service import GitFlicDispatchService
from ai_review.services.knowledge.block import render_knowledge_block
from ai_review.services.knowledge.schema import KnowledgeCandidate
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


def mr(identifier: int, *, status: str = "OPENED") -> GitFlicMergeRequest:
    return GitFlicMergeRequest(
        id=f"mr-{identifier}", localId=identifier, title=f"MR {identifier}",
        sourceBranch=GitFlicBranch(id=f"feature/{identifier}", title="feature", hash=HEAD),
        targetBranch=GitFlicBranch(id="main", title="main", hash=OLD),
        createdBy=GitFlicAuthor(id="author"), status=GitFlicStatus(id=status),
    )


def safe_protection(template: str = "ai-review-control") -> GitFlicBranchProtection:
    return GitFlicBranchProtection(
        branchTemplate=template, allowedToPush="ADMINS", allowForcePush=False
    )


@pytest.mark.asyncio
async def test_service_all_sorts_snapshot_and_starts_actionable_mrs() -> None:
    client = AsyncMock()
    client.list_open_mrs_strict.return_value = (mr(2), mr(1))
    client.list_branch_protections.return_value = (safe_protection(),)
    client.get_mr_strict.side_effect = [mr(1), mr(1), mr(2), mr(2)]
    client.get_discussions_strict.return_value = ()
    client.start_pipeline.side_effect = [
        GitFlicPipelineStartResponse(localId=1001),
        GitFlicPipelineStartResponse(localId=1002),
    ]
    service = GitFlicDispatchService(client, "owner", "project", "ai-review-control", TRUSTED)

    report = await service.run(DispatchSelection(all_open=True))

    assert report.status == "success"
    assert [item.mr_id for item in report.items] == [1, 2]
    assert report.started_count == 2
    assert client.start_pipeline.await_count == 2


@pytest.mark.asyncio
async def test_service_rejects_unsafe_overlapping_protection_before_post() -> None:
    client = AsyncMock()
    client.list_branch_protections.return_value = (
        safe_protection(), safe_protection("ai-review-*"),
    )
    service = GitFlicDispatchService(client, "owner", "project", "ai-review-control", TRUSTED)

    report = await service.run(DispatchSelection(merge_request_ids=(7,)))

    assert (report.status, report.failure_scope) == ("fatal", "security")
    client.get_mr_strict.assert_not_awaited()
    client.start_pipeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_rechecks_and_skips_when_current_summary_appears() -> None:
    client = AsyncMock()
    client.list_branch_protections.return_value = (safe_protection(),)
    client.get_mr_strict.side_effect = [mr(7), mr(7)]
    summary = thread(ThreadKind.SUMMARY, marked(MarkerKind.SUMMARY, status="complete"))
    client.get_discussions_strict.side_effect = [(), ()]
    service = GitFlicDispatchService(client, "owner", "project", "ai-review-control", TRUSTED)
    service._threads = AsyncMock(side_effect=[(), (summary,)])

    report = await service.run(DispatchSelection(merge_request_ids=(7,)))

    assert report.items[0].outcome == "skipped"
    client.start_pipeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_explicit_closed_mr_is_failed_even_with_terminal_summary() -> None:
    client = AsyncMock()
    client.list_branch_protections.return_value = (safe_protection(),)
    client.get_mr_strict.return_value = mr(7, status="CLOSED")
    summary = thread(ThreadKind.SUMMARY, marked(MarkerKind.SUMMARY, status="complete"))
    service = GitFlicDispatchService(client, "owner", "project", "ai-review-control", TRUSTED)
    service._threads = AsyncMock(return_value=(summary,))

    report = await service.run(DispatchSelection(merge_request_ids=(7,)))

    assert (report.status, report.items[0].outcome) == ("partial", "failed")
    client.start_pipeline.assert_not_awaited()


def test_dispatch_selection_deduplicates_before_limit() -> None:
    selection = DispatchSelection(merge_request_ids=(1, 2, 1, 2))

    assert selection.merge_request_ids == (1, 2)


@pytest.mark.asyncio
async def test_service_all_budget_and_empty_snapshot_are_preflight_only() -> None:
    over = AsyncMock()
    over.list_open_mrs_strict.return_value = tuple(mr(value) for value in range(1, 12))
    empty = AsyncMock()
    empty.list_open_mrs_strict.return_value = ()

    over_report = await GitFlicDispatchService(
        over, "owner", "project", "ai-review-control", TRUSTED
    ).run(DispatchSelection(all_open=True))
    empty_report = await GitFlicDispatchService(
        empty, "owner", "project", "ai-review-control", TRUSTED
    ).run(DispatchSelection(all_open=True))

    assert over_report.error_code == "request_budget_exceeded"
    assert empty_report.status == "success"
    over.list_branch_protections.assert_not_awaited()
    empty.list_branch_protections.assert_not_awaited()
    over.start_pipeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_item_failure_continues_and_pipeline_body_is_exact() -> None:
    client = AsyncMock()
    client.list_branch_protections.return_value = (safe_protection(),)
    client.get_mr_strict.side_effect = [RuntimeError("first"), mr(2), mr(2)]
    client.get_discussions_strict.return_value = ()
    client.start_pipeline.return_value = GitFlicPipelineStartResponse(localId=1002)

    report = await GitFlicDispatchService(
        client, "owner", "project", "ai-review-control", TRUSTED
    ).run(DispatchSelection(merge_request_ids=(1, 2)))

    assert (report.status, report.failed_count, report.started_count) == ("partial", 1, 1)
    request = client.start_pipeline.await_args.args[2]
    assert {item.key: item.value for item in request.variables} == {
        "AI_REVIEW_REQUESTED": "true",
        "AI_REVIEW_MODE": "initial",
        "AI_REVIEW_MR_ID": "2",
        "AI_REVIEW_HEAD_SHA": HEAD,
        "AI_REVIEW_SOURCE_BRANCH": "feature/2",
        "AI_REVIEW_TARGET_BRANCH": "main",
    }


def test_dispatch_selection_applies_limit_after_deduplication() -> None:
    values = tuple(range(1, 11)) + tuple(range(1, 11))
    assert len(DispatchSelection(merge_request_ids=values).merge_request_ids) == 10

    with pytest.raises(ValueError):
        DispatchSelection(merge_request_ids=tuple(range(1, 12)))
    with pytest.raises(ValueError):
        DispatchSelection(merge_request_ids=(0,))


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_classify_dispatch_covered_reply_is_none(version: str) -> None:
    reply_id = "11111111-1111-4111-8111-111111111111"
    reply = ReviewCommentSchema(id=reply_id, body="fixed", parent_id="SUMMARY-thread")
    values = {}
    if version == "v2":
        values = {"origin": "SUMMARY-thread", "publication": "c" * 64}
    followup = ReviewCommentSchema(
        id="followup", parent_id="SUMMARY-thread", author=UserSchema(id=TRUSTED),
        body=decorate_ai_message("done", ReviewMarker(
            version=version, kind=MarkerKind.FOLLOWUP, head=HEAD,
            covered=(reply_id,), verdict="open", **values,
        )),
    )
    summary = thread(
        ThreadKind.SUMMARY, marked(MarkerKind.SUMMARY, status="complete"),
        reply, followup,
    )

    assert classify_dispatch((summary,), TRUSTED, HEAD).mode is DispatchMode.NONE


def test_classify_dispatch_knowledge_requires_resolve_but_damaged_block_does_not() -> None:
    reply = ReviewCommentSchema(id="reply", body="rule", parent_id="SUMMARY-thread")
    base = ReviewMarker(
        version="v2", kind=MarkerKind.FOLLOWUP, head=HEAD, covered=("reply",),
        verdict="open", origin="SUMMARY-thread", publication="d" * 64,
    )
    knowledge = render_knowledge_block((KnowledgeCandidate(
        source_reply_id="reply", source_quote="rule", type="new_check",
        rule="Check the rule", rationale="Confirmed by reviewer",
    ),))
    valid = ReviewCommentSchema(
        id="valid", parent_id="SUMMARY-thread", author=UserSchema(id=TRUSTED),
        body=decorate_ai_message("done\n\n" + knowledge, base),
    )
    damaged = valid.model_copy(update={"body": valid.body.replace('"rules"', '"broken"')})
    root = marked(MarkerKind.SUMMARY, status="complete")

    valid_decision = classify_dispatch(
        (thread(ThreadKind.SUMMARY, root, reply, valid),), TRUSTED, HEAD
    )
    damaged_decision = classify_dispatch(
        (thread(ThreadKind.SUMMARY, root, reply, damaged),), TRUSTED, HEAD
    )

    assert valid_decision.mode is DispatchMode.FOLLOWUP
    assert damaged_decision.mode is DispatchMode.NONE


def test_classify_dispatch_resolved_terminal_followup_is_none() -> None:
    reply = ReviewCommentSchema(id="reply", body="fixed", parent_id="SUMMARY-thread")
    followup = ReviewCommentSchema(
        id="followup", parent_id="SUMMARY-thread", author=UserSchema(id=TRUSTED),
        body=decorate_ai_message("done", ReviewMarker(
            version="v2", kind=MarkerKind.FOLLOWUP, head=HEAD, covered=("reply",),
            verdict="fixed", origin="SUMMARY-thread", publication="e" * 64,
        )),
    )
    summary = thread(
        ThreadKind.SUMMARY, marked(MarkerKind.SUMMARY, status="complete"),
        reply, followup, resolved=True,
    )

    assert classify_dispatch((summary,), TRUSTED, HEAD).mode is DispatchMode.NONE


def test_classify_dispatch_ignores_untrusted_root_marker() -> None:
    root = ReviewCommentSchema(
        id="root", body=marked(MarkerKind.SUMMARY, status="complete"),
        author=UserSchema(id="attacker"),
    )
    fake = ReviewThreadSchema(
        id="fake", kind=ThreadKind.SUMMARY, comments=[root], resolved=False
    )

    assert classify_dispatch((fake,), TRUSTED, HEAD).mode is DispatchMode.INITIAL
