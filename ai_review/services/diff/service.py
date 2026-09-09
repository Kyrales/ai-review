from pathlib import PurePosixPath

from ai_review.config import settings
from ai_review.libs.config.review import ReviewMode
from ai_review.libs.diff.models import Diff
from ai_review.libs.diff.parser import DiffParser
from ai_review.libs.logger import get_logger
from ai_review.services.diff.renderers import (
    build_full_file_diff,
    build_full_file_current,
    build_full_file_previous,
    build_only_added,
    build_only_removed,
    build_added_and_removed,
    build_only_added_with_context,
    build_only_removed_with_context,
    build_added_and_removed_with_context,
)
from ai_review.services.diff.schema import DiffFileSchema
from ai_review.services.diff.one_c import filter_role_restriction_templates
from ai_review.services.diff.tools import find_diff_file
from ai_review.services.diff.tools import read_snapshot
from ai_review.services.diff.types import DiffServiceProtocol
from ai_review.services.git.types import GitServiceProtocol

logger = get_logger("DIFF_SERVICE")


class DiffService(DiffServiceProtocol):
    @classmethod
    def parse(cls, raw_diff: str) -> Diff:
        if not raw_diff.strip():
            logger.debug("Received empty diff string")
            return Diff(files=[], raw=raw_diff)

        try:
            return DiffParser.parse(raw_diff)
        except Exception as error:
            logger.exception(f"Failed to parse diff: {error}")
            raise

    @classmethod
    def render_file(
        cls,
        file: str,
        raw_diff: str,
        base_sha: str | None = None,
        head_sha: str | None = None,
    ) -> DiffFileSchema:
        diff = cls.parse(raw_diff)
        target = find_diff_file(diff, file)

        match settings.review.mode:
            case ReviewMode.FULL_FILE_CURRENT:
                file_diff = build_full_file_current(target, file, head_sha)
            case ReviewMode.FULL_FILE_PREVIOUS:
                file_diff = build_full_file_previous(target, file, base_sha)
            case ReviewMode.FULL_FILE_DIFF:
                file_diff = build_full_file_diff(target)
            case ReviewMode.ONLY_ADDED:
                file_diff = build_only_added(target)
            case ReviewMode.ONLY_REMOVED:
                file_diff = build_only_removed(target)
            case ReviewMode.ADDED_AND_REMOVED:
                file_diff = build_added_and_removed(target)
            case ReviewMode.ONLY_ADDED_WITH_CONTEXT:
                file_diff = build_only_added_with_context(
                    target, settings.review.context_lines
                )
            case ReviewMode.ONLY_REMOVED_WITH_CONTEXT:
                file_diff = build_only_removed_with_context(
                    target, settings.review.context_lines
                )
            case ReviewMode.ADDED_AND_REMOVED_WITH_CONTEXT:
                file_diff = build_added_and_removed_with_context(
                    target, settings.review.context_lines
                )
            case _:
                file_diff = f"# Unsupported mode: {settings.review.mode}"

        rendered = DiffFileSchema(
            diff=file_diff,
            file=file,
            added_lines=target.added_line_numbers() if target else set(),
        )
        ignored_templates = set(settings.review.ignore_1c_role_restriction_templates)
        if (
            ignored_templates
            and PurePosixPath(file.replace("\\", "/")).name == "Rights.rights"
        ):
            rendered = filter_role_restriction_templates(
                rendered,
                current=read_snapshot(file, head_sha=head_sha, workspace_fallback=False)
                if head_sha
                else None,
                previous=read_snapshot(
                    file, base_sha=base_sha, workspace_fallback=False
                )
                if base_sha
                else None,
                names=ignored_templates,
                mode=settings.review.mode,
            )
        return rendered

    @classmethod
    def render_files(
        cls,
        git: GitServiceProtocol,
        files: list[str],
        base_sha: str,
        head_sha: str,
    ) -> list[DiffFileSchema]:
        annotated: list[DiffFileSchema] = []
        for file in files:
            raw_diff = git.get_diff_for_file(base_sha, head_sha, file)
            if not raw_diff.strip():
                logger.debug(f"No diff for {file}, skipping")
                continue

            annotated.append(
                cls.render_file(
                    file=file,
                    base_sha=base_sha,
                    head_sha=head_sha,
                    raw_diff=raw_diff,
                )
            )

        return annotated
