from urllib.parse import quote

from httpx import AsyncBaseTransport, AsyncClient, AsyncHTTPTransport, Response

from ai_review.clients.gitflic.schema import (
    GitFlicChanges,
    GitFlicCreateDiscussion,
    GitFlicDiscussion,
    GitFlicDiscussionsPage,
    GitFlicMergeRequest,
    GitFlicNote,
    GitFlicReply,
)
from ai_review.config import settings
from ai_review.libs.http.client import HTTPClient
from ai_review.libs.http.handlers import HTTPClientError, handle_http_error
from ai_review.libs.http.transports.retry import NO_RETRY, RetryTransport
from ai_review.libs.logger import get_logger


class GitFlicHTTPClientError(HTTPClientError):
    pass


class GitFlicHTTPClient(HTTPClient):
    def __init__(self, transport: AsyncBaseTransport | None = None) -> None:
        retry_transport = RetryTransport(
            logger=get_logger("GITFLIC_HTTP_CLIENT"),
            transport=transport or AsyncHTTPTransport(verify=settings.vcs.http_client.verify),
            max_retries=3,
        )
        http = AsyncClient(
            base_url=settings.vcs.http_client.api_url_value.rstrip("/"),
            headers={"Authorization": f"token {settings.vcs.http_client.api_token_value}"},
            timeout=settings.vcs.http_client.timeout,
            verify=settings.vcs.http_client.verify,
            transport=retry_transport,
        )
        super().__init__(client=http)
        self.http = http

    @staticmethod
    def _mr_path(owner: str, project: str, merge_request_id: int) -> str:
        safe_owner = quote(owner, safe="")
        safe_project = quote(project, safe="")
        return f"/project/{safe_owner}/{safe_project}/merge-request/{merge_request_id}"

    @handle_http_error(client="GitFlicHTTPClient", exception=GitFlicHTTPClientError)
    async def _get(self, url: str, *, params: dict[str, int] | None = None) -> Response:
        return await self.client.get(url, params=params, follow_redirects=True)

    @handle_http_error(client="GitFlicHTTPClient", exception=GitFlicHTTPClientError)
    async def _post(self, url: str, *, json: dict[str, object] | None = None) -> Response:
        return await self.client.request("POST", url, json=json, extensions=NO_RETRY)

    @handle_http_error(client="GitFlicHTTPClient", exception=GitFlicHTTPClientError)
    async def _delete(self, url: str) -> Response:
        return await self.client.request("DELETE", url, extensions=NO_RETRY)

    async def get_mr(self, owner: str, project: str, merge_request_id: int) -> GitFlicMergeRequest:
        response = await self._get(self._mr_path(owner, project, merge_request_id))
        return GitFlicMergeRequest.model_validate_json(response.text)

    async def get_changes(self, owner: str, project: str, merge_request_id: int) -> GitFlicChanges:
        path = f"{self._mr_path(owner, project, merge_request_id)}/changes"
        changes: GitFlicChanges | None = None
        commit_blobs = []
        page_number = 0

        while True:
            response = await self._get(path, params={
                "page": page_number,
                "size": settings.vcs.pagination.per_page,
            })
            page = GitFlicChanges.model_validate_json(response.text)
            changes = page
            commit_blobs.extend(page.commitBlobs)

            page_number += 1
            if page_number >= page.page.totalPages:
                return changes.model_copy(update={"commitBlobs": commit_blobs})
            if settings.vcs.pagination.max_pages and page_number >= settings.vcs.pagination.max_pages:
                raise RuntimeError(f"Pagination exceeded max_pages={settings.vcs.pagination.max_pages}")

    async def get_discussions(
        self,
        owner: str,
        project: str,
        merge_request_id: int,
    ) -> list[GitFlicDiscussion]:
        path = f"{self._mr_path(owner, project, merge_request_id)}/discussions"
        discussions: list[GitFlicDiscussion] = []
        page_number = 0

        while True:
            response = await self._get(path, params={
                "page": page_number,
                "size": settings.vcs.pagination.per_page,
            })
            page = GitFlicDiscussionsPage.model_validate_json(response.text)
            discussions.extend(
                GitFlicDiscussion(
                    **envelope.rootNote.model_dump(),
                    replies=envelope.replies,
                )
                for envelope in page.embedded.restDiscussionModelList
            )

            page_number += 1
            if page_number >= page.page.totalPages:
                return discussions
            if settings.vcs.pagination.max_pages and page_number >= settings.vcs.pagination.max_pages:
                raise RuntimeError(f"Pagination exceeded max_pages={settings.vcs.pagination.max_pages}")

    async def create_discussion(
        self,
        owner: str,
        project: str,
        merge_request_id: int,
        request: GitFlicCreateDiscussion,
    ) -> GitFlicNote:
        response = await self._post(
            f"{self._mr_path(owner, project, merge_request_id)}/discussions/create",
            json=request.model_dump(exclude_unset=True),
        )
        return GitFlicNote.model_validate_json(response.text)

    async def reply(
        self,
        owner: str,
        project: str,
        merge_request_id: int,
        discussion_uuid: str,
        message: str,
    ) -> GitFlicNote:
        request = GitFlicReply(discussionUuid=discussion_uuid, message=message)
        response = await self._post(
            f"{self._mr_path(owner, project, merge_request_id)}/discussions/reply",
            json=request.model_dump(),
        )
        return GitFlicNote.model_validate_json(response.text)

    async def resolve(
        self,
        owner: str,
        project: str,
        merge_request_id: int,
        discussion_uuid: str,
    ) -> GitFlicNote:
        safe_uuid = quote(discussion_uuid, safe="")
        response = await self._post(
            f"{self._mr_path(owner, project, merge_request_id)}/discussions/resolve/{safe_uuid}"
        )
        return GitFlicNote.model_validate_json(response.text)

    async def delete(
        self,
        owner: str,
        project: str,
        merge_request_id: int,
        discussion_uuid: str,
    ) -> None:
        safe_uuid = quote(discussion_uuid, safe="")
        await self._delete(
            f"{self._mr_path(owner, project, merge_request_id)}/discussions/delete/{safe_uuid}"
        )

    async def aclose(self) -> None:
        await self.http.aclose()


def get_gitflic_http_client() -> GitFlicHTTPClient:
    return GitFlicHTTPClient()
