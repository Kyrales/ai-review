from ai_review.libs.asynchronous.gather import bounded_gather
from ai_review.libs.logger import get_logger
from ai_review.services.cost.types import CostServiceProtocol
from ai_review.services.diff.types import DiffServiceProtocol
from ai_review.services.git.types import GitServiceProtocol
from ai_review.services.hook import hook
from ai_review.services.policy.types import PolicyServiceProtocol
from ai_review.services.prompt.adapter import build_prompt_context_from_review_info
from ai_review.services.prompt.types import PromptServiceProtocol
from ai_review.services.review.gateway.types import ReviewLLMGatewayProtocol, ReviewCommentGatewayProtocol
from ai_review.services.review.internal.inline.types import InlineCommentServiceProtocol
from ai_review.services.review.internal.inline.schema import InlineCommentListSchema
from ai_review.services.review.runner.types import ReviewRunnerProtocol
from ai_review.services.vcs.types import ReviewInfoSchema, VCSClientProtocol
from ai_review.config import settings
from ai_review.libs.constants.vcs_provider import VCSProvider
from ai_review.libs.config.review import ReviewMode
from ai_review.services.vcs.gitflic.markers import MarkerKind, ReviewMarker, decorate_ai_message

logger = get_logger("INLINE_REVIEW_RUNNER")

INLINE_COMMENTABLE_MODES = {
    ReviewMode.FULL_FILE_DIFF,
    ReviewMode.FULL_FILE_CURRENT,
    ReviewMode.ONLY_ADDED,
    ReviewMode.ADDED_AND_REMOVED,
    ReviewMode.ONLY_ADDED_WITH_CONTEXT,
    ReviewMode.ADDED_AND_REMOVED_WITH_CONTEXT,
}


class InlineReviewRunner(ReviewRunnerProtocol):
    def __init__(
            self,
            vcs: VCSClientProtocol,
            git: GitServiceProtocol,
            diff: DiffServiceProtocol,
            cost: CostServiceProtocol,
            prompt: PromptServiceProtocol,
            policy: PolicyServiceProtocol,
            inline_comment: InlineCommentServiceProtocol,
            review_llm_gateway: ReviewLLMGatewayProtocol,
            review_comment_gateway: ReviewCommentGatewayProtocol,
    ):
        self.vcs = vcs
        self.git = git
        self.diff = diff
        self.cost = cost
        self.prompt = prompt
        self.policy = policy
        self.inline_comment = inline_comment
        self.review_llm_gateway = review_llm_gateway
        self.review_comment_gateway = review_comment_gateway

    async def analyze_file(self, file: str, review_info: ReviewInfoSchema) -> InlineCommentListSchema:
        raw_diff = self.git.get_diff_for_file(review_info.base_sha, review_info.head_sha, file)
        if not raw_diff.strip():
            logger.debug(f"No diff for {file}, skipping")
            return InlineCommentListSchema(root=[])
        if settings.review.mode not in INLINE_COMMENTABLE_MODES:
            logger.warning(
                f"Skipping inline review for {file}: mode {settings.review.mode} has no commentable added lines"
            )
            return InlineCommentListSchema(root=[])

        rendered_file = self.diff.render_file(
            file=file,
            base_sha=review_info.base_sha,
            head_sha=review_info.head_sha,
            raw_diff=raw_diff,
        )
        prompt_context = build_prompt_context_from_review_info(review_info)
        prompt = self.prompt.build_inline_request(rendered_file, prompt_context)
        prompt_system = self.prompt.build_system_inline_request(prompt_context)
        prompt_result = await self.review_llm_gateway.ask(prompt, prompt_system)

        comments = self.inline_comment.parse_model_output(prompt_result).dedupe()
        valid_comments = [
            comment for comment in comments.root
            if comment.file == file
            and comment.line in rendered_file.added_lines
        ]
        if len(valid_comments) != len(comments.root):
            logger.warning(
                f"Discarded {len(comments.root) - len(valid_comments)} inline comments outside added lines in {file}"
            )
        comments.root = valid_comments
        comments.root = self.policy.apply_for_inline_comments(comments.root)
        if not comments.root:
            logger.info(f"No inline comments for file: {file}")
            return comments

        if settings.vcs.provider is VCSProvider.GITFLIC:
            for comment in comments.root:
                comment.message = decorate_ai_message(
                    comment.body,
                    ReviewMarker(kind=MarkerKind.FINDING, head=review_info.head_sha),
                )
                comment.suggestion = None

        return comments

    async def process_file(self, file: str, review_info: ReviewInfoSchema) -> None:
        comments = await self.analyze_file(file, review_info)
        if comments.root:
            logger.info(f"Posting {len(comments.root)} inline comments to {file}")
            await self.review_comment_gateway.process_inline_comments(comments)

    async def run(self) -> None:
        await hook.emit_inline_review_start()

        comments = await self.review_comment_gateway.get_inline_comments()
        if comments:
            logger.info(f"Detected {len(comments)} existing AI inline comments, skipping inline review")
            return

        review_info = await self.vcs.get_review_info()
        logger.info(f"Starting inline review: {len(review_info.changed_files)} files changed")

        changed_files = self.policy.apply_for_files(review_info.changed_files)
        results = await bounded_gather([
            self.analyze_file(changed_file, review_info)
            for changed_file in changed_files
        ])
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            raise RuntimeError(f"{len(failures)} inline review units failed") from failures[0]

        for file, comments in zip(changed_files, results, strict=True):
            if comments.root:
                logger.info(f"Posting {len(comments.root)} inline comments to {file}")
                await self.review_comment_gateway.process_inline_comments(comments)
        await hook.emit_inline_review_complete(self.cost.aggregate())
