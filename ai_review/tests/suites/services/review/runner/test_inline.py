import pytest

from ai_review.config import settings
from ai_review.libs.constants.vcs_provider import VCSProvider
from ai_review.libs.config.review import ReviewMode
from ai_review.services.review.runner.inline import InlineReviewRunner, _split_rendered_file
from ai_review.services.diff.schema import DiffFileSchema
from ai_review.services.review.internal.inline.schema import InlineCommentSchema
from ai_review.services.diff.service import DiffService
from ai_review.services.vcs.gitflic.markers import MarkerKind, parse_marker
from ai_review.services.vcs.types import ReviewInfoSchema, ReviewCommentSchema
from ai_review.tests.fixtures.services.cost import FakeCostService
from ai_review.tests.fixtures.services.diff import FakeDiffService
from ai_review.tests.fixtures.services.git import FakeGitService
from ai_review.tests.fixtures.services.policy import FakePolicyService
from ai_review.tests.fixtures.services.prompt import FakePromptService
from ai_review.tests.fixtures.services.review.gateway.review_comment_gateway import FakeReviewCommentGateway
from ai_review.tests.fixtures.services.review.gateway.review_direct_llm_gateway import FakeReviewDirectLLMGateway
from ai_review.tests.fixtures.services.review.internal.inline import FakeInlineCommentService
from ai_review.tests.fixtures.services.vcs import FakeVCSClient


@pytest.mark.asyncio
async def test_gitflic_finding_gets_trusted_runtime_marker(
        monkeypatch, inline_review_runner: InlineReviewRunner, fake_git_service,
        fake_review_comment_gateway, fake_inline_comment_service,
):
    monkeypatch.setattr(settings.vcs, "provider", VCSProvider.GITFLIC)
    fake_git_service.responses["get_diff_for_file"] = "FAKE_DIFF"
    fake_inline_comment_service.comments = [
        InlineCommentSchema(file="main.py", line=1, message="Test comment"),
    ]
    head = "a" * 40

    await inline_review_runner.process_file("main.py", ReviewInfoSchema(
        changed_files=["main.py"], base_sha="b" * 40, head_sha=head,
    ))

    comments = next(call[1]["comments"] for call in fake_review_comment_gateway.calls if call[0] == "process_inline_comments")
    marker = parse_marker(comments.root[0].message, "owner", "owner")
    assert marker is not None
    assert marker.kind is MarkerKind.FINDING
    assert marker.head == head


@pytest.mark.asyncio
async def test_run_happy_path(
        inline_review_runner: InlineReviewRunner,
        fake_vcs_client: FakeVCSClient,
        fake_git_service: FakeGitService,
        fake_diff_service: FakeDiffService,
        fake_cost_service: FakeCostService,
        fake_prompt_service: FakePromptService,
        fake_policy_service: FakePolicyService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_review_direct_llm_gateway: FakeReviewDirectLLMGateway,
):
    """Should process all changed files, call LLM and post inline comments."""
    fake_git_service.responses["get_diff_for_file"] = "FAKE_DIFF"
    fake_review_comment_gateway.responses["get_inline_comments"] = []

    await inline_review_runner.run()

    vcs_calls = [call[0] for call in fake_vcs_client.calls]
    assert "get_review_info" in vcs_calls

    git_calls = [call[0] for call in fake_git_service.calls]
    assert any(call == "get_diff_for_file" for call in git_calls)

    assert any(call[0] == "render_file" for call in fake_diff_service.calls)
    assert any(call[0] == "apply_for_files" for call in fake_policy_service.calls)
    assert any(call[0] == "build_inline_request" for call in fake_prompt_service.calls)
    assert any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)
    assert any(call[0] == "process_inline_comments" for call in fake_review_comment_gateway.calls)

    assert any(call[0] == "aggregate" for call in fake_cost_service.calls)


@pytest.mark.asyncio
async def test_run_continues_when_any_llm_unit_fails(
        monkeypatch,
        inline_review_runner: InlineReviewRunner,
        fake_git_service: FakeGitService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_review_direct_llm_gateway: FakeReviewDirectLLMGateway,
):
    fake_git_service.responses["get_diff_for_file"] = "FAKE_DIFF"
    fake_review_comment_gateway.responses["get_inline_comments"] = []

    async def fail(_: str, __: str) -> str:
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(fake_review_direct_llm_gateway, "ask", fail)

    await inline_review_runner.run()

    assert not any(call[0] == "process_inline_comments" for call in fake_review_comment_gateway.calls)
    assert fake_review_comment_gateway.inline_review_failures == 1


def test_split_rendered_file_preserves_source_line_numbers():
    rendered = DiffFileSchema(
        file="module.bsl",
        diff="+10: first\n 11: context\n+20: second",
        added_lines={10, 20},
    )

    parts = _split_rendered_file(rendered, 30, lambda part: f"header\n{part.diff}")

    assert [part.diff for part in parts] == ["+10: first\n 11: context", "+20: second"]
    assert [part.added_lines for part in parts] == [{10}, {20}]


def test_split_rendered_file_rejects_line_larger_than_budget():
    rendered = DiffFileSchema(file="module.bsl", diff="+10: too long", added_lines={10})

    with pytest.raises(ValueError, match="module.bsl"):
        _split_rendered_file(rendered, 5, lambda part: part.diff)


def test_split_rendered_file_checks_preamble_for_empty_diff():
    rendered = DiffFileSchema(file="module.bsl", diff="", added_lines=set())

    with pytest.raises(ValueError, match="preamble"):
        _split_rendered_file(rendered, 5, lambda part: "prompt preamble")


@pytest.mark.asyncio
async def test_run_publishes_successful_files_when_another_file_fails(
        inline_review_runner: InlineReviewRunner,
        fake_vcs_client: FakeVCSClient,
        fake_git_service: FakeGitService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_inline_comment_service: FakeInlineCommentService,
):
    fake_vcs_client.responses["get_review_info"] = ReviewInfoSchema(
        changed_files=["ok.py", "broken.py"], base_sha="A", head_sha="B",
    )
    fake_review_comment_gateway.responses["get_inline_comments"] = []
    fake_inline_comment_service.comments = [InlineCommentSchema(file="ok.py", line=1, message="finding")]

    original = inline_review_runner.analyze_file

    async def analyze(file: str, review_info: ReviewInfoSchema):
        if file == "broken.py":
            raise RuntimeError("temporary model failure")
        return await original(file, review_info)

    inline_review_runner.analyze_file = analyze
    fake_git_service.responses["get_diff_for_file"] = "FAKE_DIFF"

    await inline_review_runner.run()

    calls = [call for call in fake_review_comment_gateway.calls if call[0] == "process_inline_comments"]
    assert len(calls) == 1
    assert calls[0][1]["comments"].root[0].file == "ok.py"
    assert fake_review_comment_gateway.inline_review_failures == 1


@pytest.mark.asyncio
async def test_run_skips_when_existing_comments(
        inline_review_runner: InlineReviewRunner,
        fake_vcs_client: FakeVCSClient,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_review_direct_llm_gateway: FakeReviewDirectLLMGateway,
):
    """Should skip review if there are already existing inline comments."""
    fake_review_comment_gateway.responses["get_inline_comments"] = [
        ReviewCommentSchema(id="1", body=f"{settings.review.inline_tag} existing")
    ]

    await inline_review_runner.run()

    vcs_calls = [call[0] for call in fake_vcs_client.calls]
    assert vcs_calls == []
    assert not any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)


@pytest.mark.asyncio
async def test_process_file_skips_when_no_diff(
        inline_review_runner: InlineReviewRunner,
        fake_git_service: FakeGitService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_review_direct_llm_gateway: FakeReviewDirectLLMGateway,
):
    """Should skip processing file if no diff found."""
    fake_git_service.responses["get_diff_for_file"] = ""

    review_info = ReviewInfoSchema(base_sha="A", head_sha="B")
    await inline_review_runner.process_file("file.py", review_info)

    assert not any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)
    assert not any(call[0] == "process_inline_comments" for call in fake_review_comment_gateway.calls)


@pytest.mark.asyncio
async def test_process_file_skips_when_no_comments_after_llm(
        inline_review_runner: InlineReviewRunner,
        fake_git_service: FakeGitService,
        fake_policy_service: FakePolicyService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_inline_comment_service: FakeInlineCommentService,
        fake_review_direct_llm_gateway: FakeReviewDirectLLMGateway,
):
    """Should not post comments if model output produces no inline comments."""
    fake_git_service.responses["get_diff_for_file"] = "SOME_DIFF"
    fake_inline_comment_service.comments = []

    review_info = ReviewInfoSchema(base_sha="A", head_sha="B")
    await inline_review_runner.process_file("file.py", review_info)

    assert any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)
    assert any(call[0] == "apply_for_inline_comments" for call in fake_policy_service.calls)
    assert not any(call[0] == "process_inline_comments" for call in fake_review_comment_gateway.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [
    ReviewMode.FULL_FILE_PREVIOUS,
    ReviewMode.ONLY_REMOVED,
    ReviewMode.ONLY_REMOVED_WITH_CONTEXT,
])
async def test_process_file_skips_inline_review_for_removed_only_mode(
        monkeypatch,
        mode: ReviewMode,
        inline_review_runner: InlineReviewRunner,
        fake_git_service: FakeGitService,
        fake_review_direct_llm_gateway: FakeReviewDirectLLMGateway,
):
    monkeypatch.setattr(settings.review, "mode", mode)
    fake_git_service.responses["get_diff_for_file"] = "SOME_DIFF"

    await inline_review_runner.process_file(
        "file.py",
        ReviewInfoSchema(base_sha="A", head_sha="B"),
    )

    assert not any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)


@pytest.mark.asyncio
async def test_process_file_publishes_comments_only_for_added_lines(
        inline_review_runner: InlineReviewRunner,
        fake_git_service: FakeGitService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_inline_comment_service: FakeInlineCommentService,
):
    fake_git_service.responses["get_diff_for_file"] = (
        "diff --git a/file.py b/file.py\n"
        "--- a/file.py\n"
        "+++ b/file.py\n"
        "@@ -1,2 +1,2 @@\n"
        " unchanged\n"
        "-old\n"
        "+new"
    )
    fake_inline_comment_service.comments = [
        InlineCommentSchema(file="file.py", line=1, message="unchanged"),
        InlineCommentSchema(file="file.py", line=2, message="added"),
        InlineCommentSchema(file="a/file.py", line=2, message="another file"),
    ]
    inline_review_runner.diff = DiffService()

    await inline_review_runner.process_file(
        "file.py",
        ReviewInfoSchema(base_sha="A", head_sha="B"),
    )

    calls = [call for call in fake_review_comment_gateway.calls if call[0] == "process_inline_comments"]
    assert [comment.line for comment in calls[0][1]["comments"].root] == [2]


@pytest.mark.asyncio
async def test_run_does_not_finalize(
        inline_review_runner: InlineReviewRunner,
        fake_git_service: FakeGitService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
):
    """Finalization (batch publishing) happens at pipeline level, not inside the runner."""
    fake_git_service.responses["get_diff_for_file"] = "FAKE_DIFF"
    fake_review_comment_gateway.responses["get_inline_comments"] = []

    await inline_review_runner.run()

    assert not any(call[0] == "finalize" for call in fake_review_comment_gateway.calls)


@pytest.mark.asyncio
async def test_run_does_not_finalize_when_skipping(
        inline_review_runner: InlineReviewRunner,
        fake_review_comment_gateway: FakeReviewCommentGateway,
):
    """Should not finalize when the review is skipped due to existing comments."""
    fake_review_comment_gateway.responses["get_inline_comments"] = [
        ReviewCommentSchema(id="1", body=f"{settings.review.inline_tag} existing")
    ]

    await inline_review_runner.run()

    assert not any(call[0] == "finalize" for call in fake_review_comment_gateway.calls)
