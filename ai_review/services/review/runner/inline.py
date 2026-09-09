import asyncio
import re
from collections.abc import Callable
from pathlib import PurePosixPath

from ai_review.libs.asynchronous.gather import bounded_gather
from ai_review.libs.logger import get_logger
from ai_review.services.cost.types import CostServiceProtocol
from ai_review.services.diff.types import DiffServiceProtocol
from ai_review.services.diff.schema import DiffFileSchema
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


def _split_rendered_file(
        rendered_file,
        max_prompt_chars: int,
        build_prompt: Callable[[DiffFileSchema], str],
) -> list[DiffFileSchema]:
    """Split a rendered file by complete lines without changing source numbers."""
    lines = rendered_file.diff.splitlines()
    empty_prompt_length = len(build_prompt(DiffFileSchema(
        file=rendered_file.file,
        diff="",
        added_lines=set(),
    )))
    if empty_prompt_length > max_prompt_chars:
        raise ValueError(
            f"Inline prompt preamble for {rendered_file.file} exceeds prompt limit "
            f"({max_prompt_chars} chars)"
        )
    if not lines:
        return [rendered_file]

    parts = []
    current: list[str] = []
    current_length = 0
    line_pattern = re.compile(r"^[+ ](\d+): ")

    def make_part(part_lines: list[str]):
        added_lines = {
            int(match.group(1))
            for line in part_lines
            if (match := line_pattern.match(line))
        } & rendered_file.added_lines
        return DiffFileSchema(
            file=rendered_file.file,
            diff="\n".join(part_lines),
            added_lines=added_lines,
        )

    def append_part(part_lines: list[str]) -> None:
        part = make_part(part_lines)
        if len(build_prompt(part)) > max_prompt_chars:
            raise ValueError(
                f"Rendered diff line for {rendered_file.file} exceeds inline prompt limit "
                f"({max_prompt_chars} chars)"
            )
        parts.append(part)

    for line in lines:
        line_length = len(line) + (1 if current else 0)
        if current and empty_prompt_length + current_length + line_length > max_prompt_chars:
            append_part(current)
            current = [line]
            current_length = len(line)
        else:
            current.append(line)
            current_length += line_length
    if current:
        append_part(current)
    return parts


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
        prompt_system = self.prompt.build_system_inline_request(prompt_context)
        def build_prompt(part: DiffFileSchema) -> str:
            return self.prompt.build_inline_request(part, prompt_context)
        parts = _split_rendered_file(
            rendered_file,
            settings.review.max_inline_prompt_chars,
            build_prompt,
        )
        results = await bounded_gather([
            self.review_llm_gateway.ask(build_prompt(part), prompt_system)
            for part in parts
        ])
        parsed_comments = []
        failures = []
        for result in results:
            if isinstance(result, BaseException):
                failures.append(result)
            else:
                parsed_comments.extend(self.inline_comment.parse_model_output(result).root)
        if failures:
            self.review_comment_gateway.inline_review_failures += len(failures)
            logger.warning(
                f"Skipped {len(failures)} inline review part(s) for {file}; "
                f"successful parts are preserved"
            )
        comments = InlineCommentListSchema(root=parsed_comments).dedupe()
        valid_comments = [
            comment for comment in comments.root
            if comment.file == file
            and comment.line in rendered_file.added_lines
        ]
        if len(valid_comments) != len(comments.root):
            logger.warning(
                f"Discarded {len(comments.root) - len(valid_comments)} inline comments outside added lines in {file}"
            )
        if valid_comments:
            path = PurePosixPath(file.replace("\\", "/"))
            is_role = (
                path.suffix.lower() == ".mdo"
                and path.parent.parent.name == "Roles"
                and path.stem == path.parent.name
            )
            needs_source = path.suffix.lower() == ".bsl" or path.name == "Form.form"
            source = (
                self.git.get_file_at_commit(file, review_info.head_sha)
                if needs_source
                else None
            )
            rights = None
            if is_role:
                rights = self.git.get_file_at_commit(
                    str(path.parent / "Rights.rights"), review_info.head_sha
                )
            checked_comments = [
                comment
                for comment in valid_comments
                if not (
                    is_false_bsl_multiline_comment_finding(
                        source,
                        file=file,
                        line=comment.line,
                        message=comment.message,
                    )
                    or is_false_1c_role_missing_rights_finding(
                        rights, file=file, message=comment.message
                    )
                    or is_false_1c_form_cross_scope_id_finding(
                        source, file=file, message=comment.message
                    )
                )
            ]
            if len(checked_comments) != len(valid_comments):
                logger.warning(
                    f"Discarded {len(valid_comments) - len(checked_comments)} known false 1C finding(s) in {file}"
                )
            valid_comments = checked_comments
        comments.root = valid_comments
        comments.root = self.policy.apply_for_inline_comments(comments.root)
        if not comments.root:
            logger.info(f"No inline comments for file: {file}")
            return comments

        if settings.vcs.provider is VCSProvider.GITFLIC:
            for comment in comments.root:
                comment.replace_with_rendered_body(decorate_ai_message(
                    comment.body,
                    ReviewMarker(kind=MarkerKind.FINDING, head=review_info.head_sha),
                ))

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

        self.review_comment_gateway.inline_review_executed = True
        review_info = await self.vcs.get_review_info()
        logger.info(f"Starting inline review: {len(review_info.changed_files)} files changed")

        changed_files = self.policy.apply_for_files(review_info.changed_files)
        semaphore = asyncio.Semaphore(settings.core.concurrency)

        async def review_file(file: str):
            async with semaphore:
                try:
                    return file, await self.analyze_file(file, review_info)
                except Exception as error:
                    return file, error

        tasks = [asyncio.create_task(review_file(changed_file)) for changed_file in changed_files]
        for completed in asyncio.as_completed(tasks):
            file, comments = await completed
            if isinstance(comments, BaseException):
                self.review_comment_gateway.inline_review_failures += 1
                logger.warning(f"Skipped inline review file after failure ({file}): {comments}")
                continue
            if comments.root:
                logger.info(f"Posting {len(comments.root)} inline comments to {file}")
                try:
                    await self.review_comment_gateway.process_inline_comments(comments)
                except Exception as error:
                    self.review_comment_gateway.inline_review_failures += 1
                    logger.exception(f"Failed to publish inline comments for {file}: {error}")
        await hook.emit_inline_review_complete(self.cost.aggregate())
