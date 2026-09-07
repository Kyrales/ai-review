import os
from uuid import UUID

from ai_review.libs.llm.output_json_parser import LLMOutputJSONParser
from ai_review.services.git.types import GitServiceProtocol
from ai_review.services.review.gateway.types import ReviewLLMGatewayProtocol
from ai_review.services.review.internal.followup.schema import FollowupReply
from ai_review.services.vcs.gitflic.markers import MarkerKind, ReviewMarker, decorate_ai_message, parse_marker
from ai_review.services.vcs.types import ReviewThreadSchema, SupportsResolvableThreads, VCSClientProtocol


class FollowupReviewRunner:
    """Safely handles only replies that appeared after the last trusted AI follow-up."""

    def __init__(
        self,
        vcs: VCSClientProtocol,
        review_llm_gateway: ReviewLLMGatewayProtocol,
        git: GitServiceProtocol | None = None,
    ):
        self.vcs = vcs
        self.review_llm_gateway = review_llm_gateway
        self.git = git
        self.parser = LLMOutputJSONParser(model=FollowupReply)
        self.author_id = os.environ.get("AI_REVIEW_GITFLIC_USER_ID", "")
        self.head = os.environ.get("AI_REVIEW_HEAD_SHA", "")

    def _pending(self, thread: ReviewThreadSchema) -> list[UUID]:
        covered: set[UUID] = set()
        human: list[UUID] = []
        for comment in thread.comments:
            marker = parse_marker(comment.body, comment.author.id, self.author_id)
            if marker and marker.kind is MarkerKind.FOLLOWUP:
                covered.update(marker.covered)
            elif comment.parent_id is not None:
                try:
                    human.append(UUID(str(comment.id)))
                except ValueError:
                    continue
        return [item for item in human if item not in covered][:50]

    def _last_followup_is_fixed(self, thread: ReviewThreadSchema) -> bool:
        for comment in reversed(thread.comments):
            marker = parse_marker(comment.body, comment.author.id, self.author_id)
            if marker and marker.kind is MarkerKind.FOLLOWUP:
                return marker.verdict == "fixed"
        return False

    async def _build_prompt(self, thread: ReviewThreadSchema) -> str:
        discussion = "\n\n".join(comment.body[:4000] for comment in thread.comments)
        code_context = ""
        if self.git is not None and thread.file:
            review_info = await self.vcs.get_review_info()
            diff = self.git.get_diff_for_file(
                review_info.base_sha, review_info.head_sha, thread.file, unified=20,
            )[:12000]
            current = self.git.get_file_at_commit(thread.file, review_info.head_sha) or ""
            lines = current.splitlines()
            line = max(thread.line or 1, 1)
            start = max(line - 21, 0)
            end = min(line + 20, len(lines))
            excerpt = "\n".join(
                f"{number + 1}: {lines[number]}" for number in range(start, end)
            )[:12000]
            code_context = f"\n\nАктуальный diff:\n{diff}\n\nАктуальный код:\n{excerpt}"
        return (
            "Проверь ответ разработчика на замечание по фактическому актуальному коду. "
            "Верни JSON {verdict: fixed|open|clarify, message, suggestion}.\n"
            f"Обсуждение:\n{discussion}{code_context}"
        )

    async def _process(self, thread: ReviewThreadSchema) -> None:
        pending = self._pending(thread)
        if not pending:
            if self._last_followup_is_fixed(thread) and isinstance(self.vcs, SupportsResolvableThreads):
                refreshed = next((item for item in await self.vcs.get_inline_threads() if item.id == thread.id), None)
                if refreshed is not None and not self._pending(refreshed) and self._last_followup_is_fixed(refreshed):
                    await self.vcs.resolve_thread(thread.id)
            return
        prompt = await self._build_prompt(thread)
        result = self.parser.parse_output(await self.review_llm_gateway.ask(prompt, "Ты AI-ревьювер."))
        if not result:
            return
        marker = ReviewMarker(kind=MarkerKind.FOLLOWUP, head=self.head, covered=tuple(pending), verdict=result.verdict)
        message = decorate_ai_message(result.message, marker)
        await self.vcs.create_inline_reply(thread.id, message)
        if result.verdict != "fixed" or not isinstance(self.vcs, SupportsResolvableThreads):
            return
        refreshed = next((item for item in await self.vcs.get_inline_threads() if item.id == thread.id), None)
        if refreshed is not None and not self._pending(refreshed) and self._last_followup_is_fixed(refreshed):
            await self.vcs.resolve_thread(thread.id)

    async def run(self) -> None:
        if not self.author_id or len(self.head) != 40:
            raise RuntimeError("AI_REVIEW_GITFLIC_USER_ID and 40-character AI_REVIEW_HEAD_SHA are required")
        for thread in await self.vcs.get_inline_threads():
            root = thread.comments[0] if thread.comments else None
            marker = parse_marker(root.body, root.author.id, self.author_id) if root else None
            if marker and marker.kind is MarkerKind.FINDING:
                await self._process(thread)
