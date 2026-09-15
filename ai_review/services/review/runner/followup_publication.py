from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ai_review.services.vcs.markers import MarkerKind, parse_marker
from ai_review.services.vcs.types import ReviewThreadSchema, ThreadKind


RESTORE_RESOLVED_MARKER = "<!-- ai-review-restore-resolved -->"


@dataclass(frozen=True)
class PublicationResult:
    thread: ReviewThreadSchema
    origin_was_closed: bool


def needs_resolve_restore(thread: ReviewThreadSchema, ai_author_id: str) -> bool:
    if thread.resolved is not False:
        return False
    for comment in reversed(thread.comments):
        marker = parse_marker(comment.body, comment.author.id, ai_author_id)
        if marker is not None and marker.kind is MarkerKind.FOLLOWUP:
            return (
                marker.version == "v2"
                and RESTORE_RESOLVED_MARKER in comment.body
            )
    return False


class FollowupPublicationStateMachine:
    """Publishes once while preserving a human-resolved discussion state."""

    def __init__(self, vcs: object, ai_author_id: str) -> None:
        self.vcs = vcs
        self.ai_author_id = ai_author_id
        self.last_threads: list[ReviewThreadSchema] = []

    async def _threads(self) -> list[ReviewThreadSchema]:
        result = list(await self.vcs.get_inline_threads())
        get_general = getattr(self.vcs, "get_general_threads", None)
        if get_general is not None:
            result.extend(await get_general())
        self.last_threads = result
        return result

    async def find_publication(self, key: str) -> ReviewThreadSchema | None:
        for thread in await self._threads():
            for comment in thread.comments:
                marker = parse_marker(
                    comment.body, comment.author.id, self.ai_author_id
                )
                if (
                    marker is not None
                    and marker.kind is MarkerKind.FOLLOWUP
                    and marker.version == "v2"
                    and marker.publication == key
                ):
                    return thread
        return None

    async def _resolve(self, thread_id: str | int) -> None:
        resolve = getattr(self.vcs, "resolve_thread", None)
        if resolve is not None:
            await resolve(thread_id)

    async def _post_or_discover(
        self,
        post: Callable[[], Awaitable[None]],
        publication_key: str,
    ) -> ReviewThreadSchema | None:
        try:
            await post()
        except Exception:
            published = await self.find_publication(publication_key)
            if published is None:
                raise
            return published
        return None

    def _reply(self, origin: ReviewThreadSchema, message: str) -> Awaitable[None]:
        if origin.kind is ThreadKind.SUMMARY:
            return self.vcs.create_summary_reply(origin.id, message)
        return self.vcs.create_inline_reply(origin.id, message)

    async def publish(
        self,
        origin: ReviewThreadSchema,
        message: str,
        publication_key: str,
    ) -> PublicationResult:
        existing = await self.find_publication(publication_key)
        if existing is not None:
            if existing.id != origin.id and existing.resolved is not True:
                await self._resolve(existing.id)
            elif existing.id == origin.id and needs_resolve_restore(
                existing, self.ai_author_id
            ):
                await self._resolve(existing.id)
            return PublicationResult(existing, origin.resolved is True)

        current_origin = next(
            (thread for thread in self.last_threads if thread.id == origin.id), origin
        )
        was_closed = current_origin.resolved is True

        if was_closed and getattr(self.vcs, "can_reply_resolved", False):
            published = await self._post_or_discover(
                lambda: self._reply(origin, message), publication_key
            )
            return PublicationResult(published or current_origin, True)

        if was_closed and getattr(self.vcs, "can_reopen", False):
            try:
                await self.vcs.reopen_thread(origin.id)
                reopened = next(
                    (thread for thread in await self._threads() if thread.id == origin.id),
                    None,
                )
                if reopened is None or reopened.resolved is not False:
                    raise RuntimeError("discussion did not reopen")
                recovery_message = f"{message}\n\n{RESTORE_RESOLVED_MARKER}"
                published = await self._post_or_discover(
                    lambda: self._reply(origin, recovery_message),
                    publication_key,
                )
                return PublicationResult(published or reopened, True)
            finally:
                await self._resolve(origin.id)

        create_continuation = getattr(self.vcs, "create_continuation_thread", None)
        if was_closed or (current_origin.resolved is None and create_continuation):
            if create_continuation is None:
                raise RuntimeError("provider cannot publish into a resolved discussion")
            try:
                await create_continuation(origin.id, origin.file, origin.line, message)
            except Exception:
                continuation = await self.find_publication(publication_key)
                if continuation is None:
                    raise
            else:
                continuation = await self.find_publication(publication_key)
                if continuation is None:
                    raise RuntimeError("created continuation publication was not found")
            await self._resolve(continuation.id)
            return PublicationResult(continuation, was_closed)

        published = await self._post_or_discover(
            lambda: self._reply(origin, message), publication_key
        )
        return PublicationResult(published or current_origin, False)
