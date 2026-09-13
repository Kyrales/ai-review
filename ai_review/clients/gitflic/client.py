from urllib.parse import quote
import logging

from httpx import AsyncBaseTransport, AsyncClient, AsyncHTTPTransport, Response

from ai_review.clients.gitflic.schema import (
    GitFlicAuthor,
    GitFlicChanges,
    GitFlicCreateDiscussion,
    GitFlicDiscussion,
    GitFlicDiscussionsPage,
    GitFlicMergeRequest,
    GitFlicMergeRequestsPage,
    GitFlicNote,
    GitFlicReply,
)
from ai_review.libs.config.http import HTTPClientWithTokenConfig
from ai_review.libs.http.client import HTTPClient
from ai_review.libs.http.handlers import HTTPClientError, handle_http_error
from ai_review.libs.http.transports.retry import NO_RETRY, RetryTransport


logger = logging.getLogger("GITFLIC_HTTP_CLIENT")


class GitFlicHTTPClientError(HTTPClientError):
    pass


class GitFlicHTTPClient(HTTPClient):
    def __init__(
        self,
        transport: AsyncBaseTransport | None = None,
        fallback_token: str | None = None,
        config: HTTPClientWithTokenConfig | None = None,
        per_page: int | None = None,
        max_pages: int | None = None,
    ) -> None:
        if config is None:
            from ai_review.config import settings

            config = settings.vcs.http_client
            default_per_page = settings.vcs.pagination.per_page
            default_max_pages = settings.vcs.pagination.max_pages
        else:
            default_per_page = 100
            default_max_pages = 100
        retry_transport = RetryTransport(
            logger=logger,
            transport=transport
            or AsyncHTTPTransport(verify=config.verify),
            max_retries=3,
        )
        http = AsyncClient(
            base_url=config.api_url_value.rstrip("/"),
            headers={
                "Authorization": f"token {config.api_token_value}"
            },
            timeout=config.timeout,
            verify=config.verify,
            transport=retry_transport,
        )
        super().__init__(client=http)
        self.http = http
        self._fallback_token = fallback_token or getattr(
            config, "api_token_fallback_value", None
        )
        if self._fallback_token == config.api_token_value:
            self._fallback_token = None
        self._per_page = per_page or default_per_page
        self._max_pages = default_max_pages if max_pages is None else max_pages

    async def _request(self, method: str, url: str, **kwargs: object) -> Response:
        response = await self.client.request(method, url, **kwargs)
        fallback_header = (
            f"token {self._fallback_token}" if self._fallback_token else None
        )
        if (
            response.status_code not in (401, 403)
            or not fallback_header
            or response.request.headers.get("Authorization") == fallback_header
        ):
            return response
        logger.warning(
            "GitFlic rejected the primary token; retrying with the configured fallback token"
        )
        self.client.headers["Authorization"] = fallback_header
        headers = dict(kwargs.pop("headers", {}) or {})
        headers["Authorization"] = fallback_header
        return await self.client.request(method, url, headers=headers, **kwargs)

    @staticmethod
    def _mr_path(owner: str, project: str, merge_request_id: int) -> str:
        safe_owner = quote(owner, safe="")
        safe_project = quote(project, safe="")
        return f"/project/{safe_owner}/{safe_project}/merge-request/{merge_request_id}"

    @handle_http_error(client="GitFlicHTTPClient", exception=GitFlicHTTPClientError)
    async def _get(self, url: str, *, params: dict[str, int] | None = None) -> Response:
        return await self._request("GET", url, params=params, follow_redirects=True)

    @handle_http_error(client="GitFlicHTTPClient", exception=GitFlicHTTPClientError)
    async def _post(
        self, url: str, *, json: dict[str, object] | None = None
    ) -> Response:
        return await self._request("POST", url, json=json, extensions=NO_RETRY)

    @handle_http_error(client="GitFlicHTTPClient", exception=GitFlicHTTPClientError)
    async def _delete(self, url: str) -> Response:
        return await self._request("DELETE", url, extensions=NO_RETRY)

    async def get_mr(
        self, owner: str, project: str, merge_request_id: int
    ) -> GitFlicMergeRequest:
        response = await self._get(self._mr_path(owner, project, merge_request_id))
        return GitFlicMergeRequest.model_validate_json(response.text)

    async def list_open_mrs(
        self, owner: str, project: str
    ) -> list[GitFlicMergeRequest]:
        safe_owner = quote(owner, safe="")
        safe_project = quote(project, safe="")
        path = f"/project/{safe_owner}/{safe_project}/merge-request/list"
        merge_requests: list[GitFlicMergeRequest] = []
        page_number = 0

        while True:
            response = await self._get(
                path,
                params={
                    "page": page_number,
                    "size": self._per_page,
                },
            )
            page = GitFlicMergeRequestsPage.model_validate_json(response.text)
            merge_requests.extend(
                item
                for item in page.embedded.mergeRequestModelList
                if item.status is not None and item.status.id == "OPENED"
            )

            page_number += 1
            if page_number >= page.page.totalPages:
                return merge_requests
            if (
                self._max_pages
                and page_number >= self._max_pages
            ):
                raise RuntimeError(
                    f"Pagination exceeded max_pages={self._max_pages}"
                )

    async def get_changes(
        self, owner: str, project: str, merge_request_id: int
    ) -> GitFlicChanges:
        path = f"{self._mr_path(owner, project, merge_request_id)}/changes"
        changes: GitFlicChanges | None = None
        commit_blobs = []
        page_number = 0

        while True:
            response = await self._get(
                path,
                params={
                    "page": page_number,
                    "size": self._per_page,
                },
            )
            page = GitFlicChanges.model_validate_json(response.text)
            changes = page
            commit_blobs.extend(page.commitBlobs)

            page_number += 1
            if page_number >= page.page.totalPages:
                return changes.model_copy(update={"commitBlobs": commit_blobs})
            if (
                self._max_pages
                and page_number >= self._max_pages
            ):
                raise RuntimeError(
                    f"Pagination exceeded max_pages={self._max_pages}"
                )

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
            response = await self._get(
                path,
                params={
                    "page": page_number,
                    "size": self._per_page,
                },
            )
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
            if (
                self._max_pages
                and page_number >= self._max_pages
            ):
                raise RuntimeError(
                    f"Pagination exceeded max_pages={self._max_pages}"
                )

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

    async def get_authenticated_user(self) -> GitFlicAuthor:
        response = await self._get("/user/me")
        return GitFlicAuthor.model_validate_json(response.text)



def get_gitflic_http_client() -> GitFlicHTTPClient:
    return GitFlicHTTPClient()
