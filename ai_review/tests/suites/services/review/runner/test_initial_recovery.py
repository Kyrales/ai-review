import pytest

from ai_review.config import settings
from ai_review.libs.constants.vcs_provider import VCSProvider
from ai_review.services.review.internal.summary.schema import SummaryCommentSchema
from ai_review.services.vcs.gitflic.markers import MarkerKind, parse_marker
from ai_review.services.vcs.types import ReviewCommentSchema, ReviewInfoSchema


HEAD = "a" * 40


def _gitflic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.vcs, "provider", VCSProvider.GITFLIC)


def _terminal_marker(gateway):
    comment = next(call[1]["comment"] for call in gateway.calls if call[0] == "process_summary_comment")
    return parse_marker(comment.text, "owner", "owner")


@pytest.mark.asyncio
async def test_partial_inline_then_summary_retry_is_terminal(
        monkeypatch, summary_review_runner, fake_vcs_client, fake_review_comment_gateway,
        fake_review_direct_llm_gateway,
):
    _gitflic(monkeypatch)
    fake_vcs_client.responses["get_review_info"] = ReviewInfoSchema(
        changed_files=["file.py"], base_sha="b" * 40, head_sha=HEAD,
    )
    fake_review_comment_gateway.responses.update({
        "get_summary_comments": [],
        "get_inline_comments": [ReviewCommentSchema(id="f1", body="finding")],
    })

    await summary_review_runner.run()

    marker = _terminal_marker(fake_review_comment_gateway)
    assert marker is not None
    assert marker.kind is MarkerKind.SUMMARY
    assert marker.status == "complete_with_partial_recovery"
    assert not any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)


@pytest.mark.asyncio
async def test_no_reviewable_changes_posts_terminal_summary_without_llm(
        monkeypatch, summary_review_runner, fake_vcs_client, fake_policy_service,
        fake_review_comment_gateway, fake_review_direct_llm_gateway,
):
    _gitflic(monkeypatch)
    fake_vcs_client.responses["get_review_info"] = ReviewInfoSchema(
        changed_files=[], base_sha="b" * 40, head_sha=HEAD,
    )
    fake_review_comment_gateway.responses.update({"get_summary_comments": [], "get_inline_comments": []})
    fake_policy_service.responses["apply_for_files"] = []

    await summary_review_runner.run()

    marker = _terminal_marker(fake_review_comment_gateway)
    assert marker is not None
    assert marker.status == "complete"
    assert not any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)


@pytest.mark.asyncio
async def test_normal_summary_is_marked_terminal_for_gitflic(
        monkeypatch, summary_review_runner, fake_vcs_client, fake_review_comment_gateway,
):
    _gitflic(monkeypatch)
    fake_vcs_client.responses["get_review_info"] = ReviewInfoSchema(
        changed_files=["file.py"], base_sha="b" * 40, head_sha=HEAD,
    )
    fake_review_comment_gateway.responses.update({"get_summary_comments": [], "get_inline_comments": []})

    await summary_review_runner.run()

    marker = _terminal_marker(fake_review_comment_gateway)
    assert marker is not None
    assert marker.status == "complete"


@pytest.mark.asyncio
async def test_empty_summary_is_terminal_with_warning_for_gitflic(
        monkeypatch, summary_review_runner, fake_vcs_client, fake_review_comment_gateway,
        fake_summary_comment_service,
):
    _gitflic(monkeypatch)
    fake_vcs_client.responses["get_review_info"] = ReviewInfoSchema(
        changed_files=["file.py"], base_sha="b" * 40, head_sha=HEAD,
    )
    fake_review_comment_gateway.responses.update({"get_summary_comments": [], "get_inline_comments": []})
    fake_summary_comment_service.responses["parse_model_output"] = SummaryCommentSchema(text="")

    await summary_review_runner.run()

    marker = _terminal_marker(fake_review_comment_gateway)
    assert marker is not None
    assert marker.status == "complete_with_warnings"
