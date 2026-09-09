import pytest

from ai_review.services.review.runner.context import ContextReviewRunner
from ai_review.services.review.internal.inline.schema import InlineCommentSchema
from ai_review.services.diff.schema import DiffFileSchema
from ai_review.services.vcs.types import ReviewCommentSchema, ReviewInfoSchema
from ai_review.tests.fixtures.services.cost import FakeCostService
from ai_review.tests.fixtures.services.diff import FakeDiffService
from ai_review.tests.fixtures.services.prompt import FakePromptService
from ai_review.tests.fixtures.services.review.gateway.review_comment_gateway import FakeReviewCommentGateway
from ai_review.tests.fixtures.services.review.gateway.review_direct_llm_gateway import FakeReviewDirectLLMGateway
from ai_review.tests.fixtures.services.review.internal.inline import FakeInlineCommentService
from ai_review.tests.fixtures.services.policy import FakePolicyService
from ai_review.tests.fixtures.services.vcs import FakeVCSClient


@pytest.mark.asyncio
async def test_run_happy_path(
        context_review_runner: ContextReviewRunner,
        fake_vcs_client: FakeVCSClient,
        fake_git_service,
        fake_diff_service: FakeDiffService,
        fake_cost_service: FakeCostService,
        fake_prompt_service: FakePromptService,
        fake_policy_service: FakePolicyService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_review_direct_llm_gateway: FakeReviewDirectLLMGateway,
):
    """Should render all changed files, call LLM and post inline comments."""
    fake_review_comment_gateway.responses["get_inline_comments"] = []

    await context_review_runner.run()

    vcs_calls = [call[0] for call in fake_vcs_client.calls]
    assert "get_review_info" in vcs_calls

    assert any(call[0] == "render_files" for call in fake_diff_service.calls)
    assert any(call[0] == "apply_for_files" for call in fake_policy_service.calls)
    assert any(call[0] == "build_context_request" for call in fake_prompt_service.calls)
    assert any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)
    assert any(call[0] == "process_inline_comments" for call in fake_review_comment_gateway.calls)

    assert any(call[0] == "aggregate" for call in fake_cost_service.calls)
    assert not any(call[0] == "get_file_at_commit" for call in fake_git_service.calls)


@pytest.mark.asyncio
async def test_run_skips_when_existing_comments(
        context_review_runner: ContextReviewRunner,
        fake_vcs_client: FakeVCSClient,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_review_direct_llm_gateway: FakeReviewDirectLLMGateway,
):
    """Should skip context review if inline comments already exist."""
    fake_review_comment_gateway.responses["get_inline_comments"] = [
        ReviewCommentSchema(id="1", body="#ai-review-inline existing"),
    ]

    await context_review_runner.run()

    vcs_calls = [call[0] for call in fake_vcs_client.calls]
    assert vcs_calls == []
    assert not any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)
    assert not any(call[0] == "finalize" for call in fake_review_comment_gateway.calls)


@pytest.mark.asyncio
async def test_run_skips_when_no_changed_files(
        context_review_runner: ContextReviewRunner,
        fake_vcs_client: FakeVCSClient,
        fake_policy_service: FakePolicyService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
):
    """Should skip when no changed files after policy filtering."""
    fake_policy_service.responses["apply_for_files"] = []
    fake_review_comment_gateway.responses["get_inline_comments"] = []

    await context_review_runner.run()

    vcs_calls = [call[0] for call in fake_vcs_client.calls]
    assert "get_review_info" in vcs_calls
    assert any(call[0] == "apply_for_files" for call in fake_policy_service.calls)
    assert not any(call[0] == "finalize" for call in fake_review_comment_gateway.calls)


@pytest.mark.asyncio
async def test_run_skips_when_no_comments_after_llm(
        context_review_runner: ContextReviewRunner,
        fake_policy_service: FakePolicyService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_inline_comment_service: FakeInlineCommentService,
        fake_review_direct_llm_gateway: FakeReviewDirectLLMGateway,

):
    """Should not post comments if LLM output is empty."""
    fake_review_comment_gateway.responses["get_inline_comments"] = []
    fake_inline_comment_service.comments = []

    await context_review_runner.run()

    assert any(call[0] == "ask" for call in fake_review_direct_llm_gateway.calls)
    assert any(call[0] == "apply_for_context_comments" for call in fake_policy_service.calls)
    assert not any(call[0] == "process_inline_comments" for call in fake_review_comment_gateway.calls)
    assert not any(call[0] == "finalize" for call in fake_review_comment_gateway.calls)


@pytest.mark.asyncio
async def test_run_publishes_context_comments_only_for_added_lines(
        context_review_runner: ContextReviewRunner,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_inline_comment_service: FakeInlineCommentService,
):
    fake_review_comment_gateway.responses["get_inline_comments"] = []
    fake_inline_comment_service.comments = [
        InlineCommentSchema(file="file.py", line=1, message="added"),
        InlineCommentSchema(file="file.py", line=2, message="unchanged"),
    ]

    await context_review_runner.run()

    call = next(
        call for call in fake_review_comment_gateway.calls
        if call[0] == "process_inline_comments"
    )
    comments = call[1]["comments"].root
    assert [(comment.file, comment.line) for comment in comments] == [("file.py", 1)]


@pytest.mark.asyncio
async def test_run_discards_false_bsl_multiline_comment_finding(
        context_review_runner: ContextReviewRunner,
        fake_vcs_client: FakeVCSClient,
        fake_git_service,
        fake_diff_service: FakeDiffService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_inline_comment_service: FakeInlineCommentService,
):
    file = "CommonModules/Test/Module.bsl"
    fake_review_comment_gateway.responses["get_inline_comments"] = []
    fake_vcs_client.responses["get_review_info"] = ReviewInfoSchema(
        changed_files=[file], base_sha="A", head_sha="B"
    )
    fake_git_service.responses["get_file_at_commit"] = (
        'Text = "first\n|second\n// author note\n|third\n";'
    )
    fake_diff_service.render_files = lambda **_: [
        DiffFileSchema(file=file, diff="FAKE_DIFF", added_lines={3})
    ]
    fake_inline_comment_service.comments = [
        InlineCommentSchema(
            file=file,
            line=3,
            message="Комментарий разрывает многострочный строковый литерал",
        )
    ]

    await context_review_runner.run()

    assert not any(
        call[0] == "process_inline_comments"
        for call in fake_review_comment_gateway.calls
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("file", "source", "message"),
    [
        (
            "src/Roles/Test/Test.mdo",
            "<rights><object><right><value>true</value></right></object></rights>",
            "Роль не содержит прав на объекты метаданных.",
        ),
        (
            "src/Catalogs/Test/Forms/ItemForm/Form.form",
            '<Form><items><id>1</id></items><attributes><id>1</id></attributes></Form>',
            (
                "Идентификатор элемента формы совпадает с идентификатором "
                "атрибута, поэтому форма не загрузится."
            ),
        ),
    ],
)
async def test_run_discards_known_false_1c_findings(
        file: str,
        source: str,
        message: str,
        context_review_runner: ContextReviewRunner,
        fake_vcs_client: FakeVCSClient,
        fake_git_service,
        fake_diff_service: FakeDiffService,
        fake_review_comment_gateway: FakeReviewCommentGateway,
        fake_inline_comment_service: FakeInlineCommentService,
):
    fake_review_comment_gateway.responses["get_inline_comments"] = []
    fake_vcs_client.responses["get_review_info"] = ReviewInfoSchema(
        changed_files=[file], base_sha="A", head_sha="B"
    )
    fake_git_service.responses["get_file_at_commit"] = source
    fake_diff_service.render_files = lambda **_: [
        DiffFileSchema(file=file, diff="FAKE_DIFF", added_lines={1})
    ]
    fake_inline_comment_service.comments = [
        InlineCommentSchema(file=file, line=1, message=message)
    ]

    await context_review_runner.run()

    assert not any(
        call[0] == "process_inline_comments"
        for call in fake_review_comment_gateway.calls
    )


@pytest.mark.asyncio
async def test_run_does_not_finalize(
        context_review_runner: ContextReviewRunner,
        fake_review_comment_gateway: FakeReviewCommentGateway,
):
    """Finalization (batch publishing) happens at pipeline level, not inside the runner."""
    fake_review_comment_gateway.responses["get_inline_comments"] = []

    await context_review_runner.run()

    assert not any(call[0] == "finalize" for call in fake_review_comment_gateway.calls)
