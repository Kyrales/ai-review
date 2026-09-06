from ai_review.clients.gitflic.client import get_gitflic_http_client
from ai_review.clients.gitflic.schema import GitFlicCreateDiscussion, GitFlicDiscussion
from ai_review.config import settings
from ai_review.services.vcs.gitflic.adapter import find_position, to_review_comment, to_review_info
from ai_review.services.vcs.types import (
    ReviewCommentSchema,
    ReviewInfoSchema,
    ReviewThreadSchema,
    ThreadKind,
    VCSClientProtocol,
)


class GitFlicVCSClient(VCSClientProtocol):
    def __init__(self) -> None:
        self.http_client = get_gitflic_http_client()
        self.owner = settings.vcs.pipeline.owner
        self.project = settings.vcs.pipeline.project
        self.merge_request_id = settings.vcs.pipeline.merge_request_id

    async def get_review_info(self) -> ReviewInfoSchema:
        mr = await self.http_client.get_mr(self.owner, self.project, self.merge_request_id)
        changes = await self.http_client.get_changes(self.owner, self.project, self.merge_request_id)
        return to_review_info(mr, [change.newPath for change in changes.commitBlobs if change.newPath])

    async def _discussions(self) -> list[GitFlicDiscussion]:
        return await self.http_client.get_discussions(self.owner, self.project, self.merge_request_id)

    @staticmethod
    def _comments(discussion: GitFlicDiscussion) -> list[ReviewCommentSchema]:
        return [
            to_review_comment(note, discussion.uuid, discussion)
            for note in [discussion, *discussion.replies]
        ]

    async def get_general_comments(self) -> list[ReviewCommentSchema]:
        return [
            to_review_comment(discussion, discussion.uuid)
            for discussion in await self._discussions()
            if discussion.newPath is None or discussion.newLine is None
        ]

    async def get_inline_comments(self) -> list[ReviewCommentSchema]:
        return [
            comment
            for discussion in await self._discussions()
            if discussion.newPath is not None and discussion.newLine is not None
            for comment in self._comments(discussion)
        ]

    async def create_general_comment(self, message: str) -> None:
        await self.http_client.create_discussion(
            self.owner,
            self.project,
            self.merge_request_id,
            request=GitFlicCreateDiscussion(message=message),
        )

    async def create_inline_comment(self, file: str, line: int, message: str) -> None:
        changes = await self.http_client.get_changes(self.owner, self.project, self.merge_request_id)
        request = find_position(changes.commitBlobs, file, line)
        if request is None:
            await self.create_general_comment(message)
            return
        await self.http_client.create_discussion(
            self.owner,
            self.project,
            self.merge_request_id,
            request=request.model_copy(update={"message": message}),
        )

    async def delete_general_comment(self, comment_id: int | str) -> None:
        await self.http_client.delete(self.owner, self.project, self.merge_request_id, str(comment_id))

    async def delete_inline_comment(self, comment_id: int | str) -> None:
        await self.delete_general_comment(comment_id)

    async def create_inline_reply(self, thread_id: int | str, message: str) -> None:
        await self.http_client.reply(self.owner, self.project, self.merge_request_id, str(thread_id), message)

    async def create_summary_reply(self, thread_id: int | str, message: str) -> None:
        await self.create_inline_reply(thread_id, message)

    async def resolve_thread(self, thread_id: int | str) -> None:
        await self.http_client.resolve(self.owner, self.project, self.merge_request_id, str(thread_id))

    async def get_inline_threads(self) -> list[ReviewThreadSchema]:
        return [
            ReviewThreadSchema(
                id=discussion.uuid,
                kind=ThreadKind.INLINE,
                file=discussion.newPath,
                line=discussion.newLine,
                comments=self._comments(discussion),
            )
            for discussion in await self._discussions()
            if discussion.newPath is not None and discussion.newLine is not None
        ]

    async def get_general_threads(self) -> list[ReviewThreadSchema]:
        return [
            ReviewThreadSchema(
                id=discussion.uuid,
                kind=ThreadKind.SUMMARY,
                comments=self._comments(discussion),
            )
            for discussion in await self._discussions()
            if discussion.newPath is None or discussion.newLine is None
        ]
