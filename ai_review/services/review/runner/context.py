from pathlib import PurePosixPath

from ai_review.config import settings
from ai_review.libs.constants.vcs_provider import VCSProvider
from ai_review.libs.logger import get_logger
from ai_review.services.cost.types import CostServiceProtocol
from ai_review.services.diff.types import DiffServiceProtocol
from ai_review.services.diff.one_c import (
    is_false_1c_form_cross_scope_id_finding,
    is_false_1c_role_missing_rights_finding,
    is_false_bsl_multiline_comment_finding,
)
from ai_review.services.git.types import GitServiceProtocol
from ai_review.services.hook import hook
from ai_review.services.policy.types import PolicyServiceProtocol
from ai_review.services.prompt.adapter import build_prompt_context_from_review_info
from ai_review.services.prompt.types import PromptServiceProtocol
from ai_review.services.review.gateway.types import ReviewLLMGatewayProtocol, ReviewCommentGatewayProtocol
from ai_review.services.review.internal.inline.types import InlineCommentServiceProtocol
from ai_review.services.review.runner.types import ReviewRunnerProtocol
from ai_review.services.vcs.types import VCSClientProtocol
from ai_review.services.vcs.gitflic.markers import MarkerKind, ReviewMarker, decorate_ai_message

logger = get_logger("CONTEXT_REVIEW_RUNNER")


class ContextReviewRunner(ReviewRunnerProtocol):
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

    async def run(self) -> None:
        await hook.emit_context_review_start()

        comments = await self.review_comment_gateway.get_inline_comments()
        if comments:
            logger.info(f"Detected {len(comments)} existing AI inline comments, skipping context review")
            return

        review_info = await self.vcs.get_review_info()
        changed_files = self.policy.apply_for_files(review_info.changed_files)
        if not changed_files:
            logger.info("No files to review for context review")
            return

        logger.info(f"Starting context inline review: {len(changed_files)} files changed")

        rendered_files = self.diff.render_files(
            git=self.git,
            files=changed_files,
            base_sha=review_info.base_sha,
            head_sha=review_info.head_sha,
        )
        prompt_context = build_prompt_context_from_review_info(review_info)
        prompt = self.prompt.build_context_request(rendered_files, prompt_context)
        prompt_system = self.prompt.build_system_context_request(prompt_context)
        prompt_result = await self.review_llm_gateway.ask(prompt, prompt_system)

        comments = self.inline_comment.parse_model_output(prompt_result).dedupe()
        added_lines = {
            (rendered_file.file, line)
            for rendered_file in rendered_files
            for line in rendered_file.added_lines
        }
        valid_comments = [
            comment for comment in comments.root
            if (comment.file, comment.line) in added_lines
        ]
        if len(valid_comments) != len(comments.root):
            logger.warning(
                f"Discarded {len(comments.root) - len(valid_comments)} context comments outside added lines"
            )
        sources: dict[str, str | None] = {}
        checked_comments = []
        for comment in valid_comments:
            path = PurePosixPath(comment.file.replace("\\", "/"))
            is_role = (
                path.suffix.lower() == ".mdo"
                and path.parent.parent.name == "Roles"
                and path.stem == path.parent.name
            )
            needs_source = path.suffix.lower() == ".bsl" or path.name == "Form.form"
            if needs_source and comment.file not in sources:
                sources[comment.file] = self.git.get_file_at_commit(
                    comment.file, review_info.head_sha
                )
            source = sources.get(comment.file)
            rights = None
            if is_role:
                rights_path = str(path.parent / "Rights.rights")
                if rights_path not in sources:
                    sources[rights_path] = self.git.get_file_at_commit(
                        rights_path, review_info.head_sha
                    )
                rights = sources[rights_path]
            if (
                is_false_bsl_multiline_comment_finding(
                    source,
                    file=comment.file,
                    line=comment.line,
                    message=comment.message,
                )
                or is_false_1c_role_missing_rights_finding(
                    rights, file=comment.file, message=comment.message
                )
                or is_false_1c_form_cross_scope_id_finding(
                    source,
                    file=comment.file,
                    message=comment.message,
                )
            ):
                continue
            checked_comments.append(comment)
        if len(checked_comments) != len(valid_comments):
            logger.warning(
                f"Discarded {len(valid_comments) - len(checked_comments)} known false 1C context finding(s)"
            )
        valid_comments = checked_comments
        comments.root = valid_comments
        comments.root = self.policy.apply_for_context_comments(comments.root)
        if not comments.root:
            logger.info("No inline comments from context review")
            return

        if settings.vcs.provider is VCSProvider.GITFLIC:
            for comment in comments.root:
                comment.replace_with_rendered_body(decorate_ai_message(
                    comment.body,
                    ReviewMarker(kind=MarkerKind.FINDING, head=review_info.head_sha),
                ))

        logger.info(f"Posting {len(comments.root)} inline comments (context review)")
        await self.review_comment_gateway.process_inline_comments(comments)
        await hook.emit_context_review_complete(self.cost.aggregate())
