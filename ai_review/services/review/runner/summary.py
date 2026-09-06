from ai_review.libs.logger import get_logger
from ai_review.services.cost.types import CostServiceProtocol
from ai_review.services.diff.types import DiffServiceProtocol
from ai_review.services.git.types import GitServiceProtocol
from ai_review.services.hook import hook
from ai_review.services.policy.types import PolicyServiceProtocol
from ai_review.services.prompt.adapter import build_prompt_context_from_review_info
from ai_review.services.prompt.types import PromptServiceProtocol
from ai_review.services.review.gateway.types import ReviewLLMGatewayProtocol, ReviewCommentGatewayProtocol
from ai_review.services.review.internal.summary.types import SummaryCommentServiceProtocol
from ai_review.services.review.runner.types import ReviewRunnerProtocol
from ai_review.services.vcs.types import VCSClientProtocol
from ai_review.services.vcs.gitflic.markers import MarkerKind, ReviewMarker, decorate_ai_message
from ai_review.config import settings
from ai_review.libs.constants.vcs_provider import VCSProvider

logger = get_logger("SUMMARY_REVIEW_RUNNER")


class SummaryReviewRunner(ReviewRunnerProtocol):
    def __init__(
            self,
            vcs: VCSClientProtocol,
            git: GitServiceProtocol,
            diff: DiffServiceProtocol,
            cost: CostServiceProtocol,
            prompt: PromptServiceProtocol,
            policy: PolicyServiceProtocol,
            summary_comment: SummaryCommentServiceProtocol,
            review_llm_gateway: ReviewLLMGatewayProtocol,
            review_comment_gateway: ReviewCommentGatewayProtocol,
    ):
        self.vcs = vcs
        self.git = git
        self.diff = diff
        self.cost = cost
        self.prompt = prompt
        self.policy = policy
        self.summary_comment = summary_comment
        self.review_llm_gateway = review_llm_gateway
        self.review_comment_gateway = review_comment_gateway

    async def post_terminal_summary(self, text: str, status: str, head_sha: str) -> None:
        summary = self.summary_comment.parse_model_output(text)
        summary.text = decorate_ai_message(
            summary.text,
            ReviewMarker(kind=MarkerKind.SUMMARY, status=status, head=head_sha),
        )
        await self.review_comment_gateway.process_summary_comment(summary)

    async def run(self) -> None:
        await hook.emit_summary_review_start()

        comments = await self.review_comment_gateway.get_summary_comments()
        if comments:
            logger.info(f"Detected {len(comments)} existing AI summary comments, skipping summary review")
            return

        review_info = await self.vcs.get_review_info()
        inline_comments = await self.review_comment_gateway.get_inline_comments()
        if inline_comments and settings.vcs.provider is VCSProvider.GITFLIC:
            await self.post_terminal_summary(
                "Initial review was partially recovered: previously published findings were kept; "
                "the remaining inline review was not regenerated.",
                status="complete_with_partial_recovery",
                head_sha=review_info.head_sha,
            )
            await hook.emit_summary_review_complete(self.cost.aggregate())
            return
        changed_files = self.policy.apply_for_files(review_info.changed_files)
        if not changed_files:
            logger.info("No files to review for summary")
            if settings.vcs.provider is VCSProvider.GITFLIC:
                await self.post_terminal_summary(
                    "No reviewable changes were found.",
                    status="complete",
                    head_sha=review_info.head_sha,
                )
                await hook.emit_summary_review_complete(self.cost.aggregate())
            return

        logger.info(f"Starting summary review: {len(changed_files)} files changed")

        rendered_files = self.diff.render_files(
            git=self.git,
            files=changed_files,
            base_sha=review_info.base_sha,
            head_sha=review_info.head_sha,
        )
        prompt_context = build_prompt_context_from_review_info(review_info)
        prompt = self.prompt.build_summary_request(rendered_files, prompt_context)
        prompt_system = self.prompt.build_system_summary_request(prompt_context)
        prompt_result = await self.review_llm_gateway.ask(prompt, prompt_system)

        summary = self.summary_comment.parse_model_output(prompt_result)
        if not summary.text.strip():
            logger.warning("Summary LLM output was empty, skipping comment")
            if settings.vcs.provider is VCSProvider.GITFLIC:
                await self.post_terminal_summary(
                    "The summary model returned an empty response; inline findings, if any, were preserved.",
                    status="complete_with_warnings",
                    head_sha=review_info.head_sha,
                )
                await hook.emit_summary_review_complete(self.cost.aggregate())
            return

        logger.info(f"Posting summary review comment ({len(summary.text)} chars)")
        if settings.vcs.provider is VCSProvider.GITFLIC:
            summary.text = decorate_ai_message(
                summary.text,
                ReviewMarker(kind=MarkerKind.SUMMARY, status="complete", head=review_info.head_sha),
            )
        await self.review_comment_gateway.process_summary_comment(summary)
        await hook.emit_summary_review_complete(self.cost.aggregate())
