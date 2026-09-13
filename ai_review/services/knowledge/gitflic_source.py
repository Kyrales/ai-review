from ai_review.clients.gitflic.client import GitFlicHTTPClient
from ai_review.libs.constants.vcs_provider import VCSProvider
from ai_review.services.vcs.gitflic.adapter import to_review_comment, to_review_summary
from ai_review.services.vcs.types import ReviewSummarySchema, ReviewThreadSchema, ThreadKind


class GitFlicKnowledgeSource:
    provider = VCSProvider.GITFLIC

    def __init__(self, client: GitFlicHTTPClient, owner: str, project: str) -> None:
        self.client = client
        self.owner = owner
        self.project = project
        self.project_key = f"{owner}/{project}"

    async def list_open_reviews(self) -> list[ReviewSummarySchema]:
        return [to_review_summary(item) for item in await self.client.list_open_mrs(self.owner, self.project)]

    async def get_review(self, review_id: str | int) -> ReviewSummarySchema:
        return to_review_summary(await self.client.get_mr(self.owner, self.project, int(review_id)))

    async def get_review_threads(self, review_id: str | int) -> list[ReviewThreadSchema]:
        discussions = await self.client.get_discussions(self.owner, self.project, int(review_id))
        return [
            ReviewThreadSchema(
                id=item.uuid,
                kind=ThreadKind.INLINE if item.newPath is not None and item.newLine is not None else ThreadKind.SUMMARY,
                file=item.newPath,
                line=item.newLine,
                resolved=item.resolved,
                comments=[to_review_comment(note, item.uuid, item) for note in [item, *item.replies]],
            )
            for item in discussions
        ]
