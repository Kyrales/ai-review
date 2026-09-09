import fnmatch

from ai_review.config import settings
from ai_review.libs.config.review import ReviewSeverity
from ai_review.libs.logger import get_logger
from ai_review.services.policy.types import PolicyServiceProtocol

logger = get_logger("REVIEW_SERVICE")


class PolicyService(PolicyServiceProtocol):
    @staticmethod
    def _filter_comments_by_severity(comments: list) -> list:
        severities = set(settings.review.publish_severities)
        filtered = [
            comment for comment in comments
            if getattr(comment, "severity", ReviewSeverity.MEDIUM) in severities
        ]
        if len(filtered) != len(comments):
            logger.info(
                f"Filtered {len(comments) - len(filtered)} comments by severity"
            )
        return filtered

    @classmethod
    def should_review_file(cls, file: str) -> bool:
        review = settings.review

        for pattern in review.ignore_changes:
            if fnmatch.fnmatch(file, pattern):
                logger.debug(f"Skipping {file} (matched ignore: {pattern})")
                return False

        if not review.allow_changes:
            logger.debug(f"Allowing {file} (no allow rules, passed ignore)")
            return True

        for pattern in review.allow_changes:
            if fnmatch.fnmatch(file, pattern):
                logger.debug(f"Allowing {file} (matched allow: {pattern})")
                return True

        logger.debug(f"Skipping {file} (did not match any allow rule)")
        return False

    @classmethod
    def should_agent_run_command(cls, command: str) -> bool:
        agent = settings.agent
        command = (command or "").strip()

        if not command:
            logger.debug("Agent command policy: blocked empty command")
            return False

        for pattern in agent.allow_commands:
            if pattern.fullmatch(command):
                logger.debug(f"Agent command policy: allow '{command}' (pattern: {pattern.pattern})")
                return True

        logger.debug(f"Agent command policy: block '{command}' (no pattern matched)")
        return False

    @classmethod
    def apply_for_files(cls, files: list[str]) -> list[str]:
        allowed = [file for file in files if cls.should_review_file(file)]
        skipped = [file for file in files if not cls.should_review_file(file)]

        if skipped:
            logger.info(f"Skipped {len(skipped)} files by policy: {skipped}")

        if allowed:
            logger.info(f"Proceeding with {len(allowed)} files after policy filter")

        return allowed

    @classmethod
    def apply_for_inline_comments(cls, comments: list) -> list:
        filtered = cls._filter_comments_by_severity(comments)
        limit = settings.review.max_inline_comments
        if limit and (len(filtered) > limit):
            logger.info(f"Limiting inline comments to {limit} (from {len(filtered)})")
            return filtered[:limit]

        return filtered

    @classmethod
    def apply_for_context_comments(cls, comments: list) -> list:
        comments = cls._filter_comments_by_severity(comments)
        limit = settings.review.max_context_comments
        if limit and (len(comments) > limit):
            logger.info(f"Limiting context comments to {limit} (from {len(comments)})")
            return comments[:limit]

        return comments
