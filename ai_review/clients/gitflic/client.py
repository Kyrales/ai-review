from urllib.parse import quote
import logging

from httpx import AsyncBaseTransport, AsyncClient, AsyncHTTPTransport, Response
from pydantic import BaseModel, ValidationError

from ai_review.clients.gitflic.schema import (
    GitFlicAuthor,
    GitFlicBranchProtection,
    GitFlicBranchProtectionsPage,
    GitFlicChanges,
    GitFlicCreateDiscussion,
    GitFlicDiscussion,
    GitFlicDiscussionsPage,
    GitFlicMergeRequest,
    GitFlicMergeRequestsPage,
    GitFlicNote,
    GitFlicPipelineStartRequest,
    GitFlicPipelineStartResponse,
    GitFlicReply,
)
from ai_review.libs.config.http import HTTPClientWithTokenConfig
from ai_review.libs.http.client import HTTPClient
from ai_review.libs.http.handlers import HTTPClientError, handle_http_error
from ai_review.libs.http.transports.retry import NO_RETRY, RetryTransport


logger = logging.getLogger("GITFLIC_HTTP_CLIENT")


class GitFlicHTTPClientError(HTTPClientError):
    pass


class GitFlicProtocolError(Exception):
    def __init__(
        self,
        endpoint_code: str,
        status_code: int,
        request_id: str | None = None,
        error_code: str = "invalid_response",
    ) -> None:
        self.endpoint_code = endpoint_code
        self.status_code = status_code
        self.request_id = request_id
        self.error_code = error_code
        super().__init__(f"GitFlic {endpoint_code}: {error_code} (HTTP {status_code})")


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

    async def _request(
        self, method: str, url: str, *, allow_fallback: bool = True, **kwargs: object
    ) -> Response:
        response = await self.client.request(method, url, **kwargs)
        fallback_header = (
            f"token {self._fallback_token}" if self._fallback_token else None
        )
        if (
            not allow_fallback
            or response.status_code not in (401, 403)
            or not fallback_header
            or response.request.headers.get("Authorization") == fallback_header
        ):
            return response
        logger.warning(
            "GitFlic rejected the primary token; retrying with the configured fallback token"
        )
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
        return await self._request("GET", url, params=params, follow_redirects=False)

    @handle_http_error(client="GitFlicHTTPClient", exception=GitFlicHTTPClientError)
    async def _post(
        self,
        url: str,
        *,
        json: dict[str, object] | None = None,
        allow_fallback: bool = True,
    ) -> Response:
        return await self._request(
            "POST", url, json=json, extensions=NO_RETRY,
            allow_fallback=allow_fallback,
        )

    @handle_http_error(client="GitFlicHTTPClient", exception=GitFlicHTTPClientError)
    async def _delete(self, url: str) -> Response:
        return await self._request("DELETE", url, extensions=NO_RETRY)

    async def get_mr(
        self, owner: str, project: str, merge_request_id: int
    ) -> GitFlicMergeRequest:
        response = await self._get(self._mr_path(owner, project, merge_request_id))
        return GitFlicMergeRequest.model_validate_json(response.text)

    async def get_mr_strict(
        self, owner: str, project: str, merge_request_id: int
    ) -> GitFlicMergeRequest:
        response = await self._get(self._mr_path(owner, project, merge_request_id))
        result = self._validate_response(response, GitFlicMergeRequest, "merge_request")
        assert isinstance(result, GitFlicMergeRequest)
        return result

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

    @staticmethod
    def _safe_request_id(response: Response) -> str | None:
        for name in ("x-request-id", "request-id"):
            value = response.headers.get(name)
            if value and len(value) <= 128 and all(32 <= ord(char) <= 126 for char in value):
                return value
        return None

    @classmethod
    def _validate_response(
        cls, response: Response, model: type[BaseModel], endpoint_code: str
    ) -> BaseModel:
        protocol_error: GitFlicProtocolError | None = None
        try:
            result = model.model_validate_json(response.content)
        except (ValidationError, ValueError, UnicodeError):
            protocol_error = GitFlicProtocolError(
                endpoint_code=endpoint_code,
                status_code=response.status_code,
                request_id=cls._safe_request_id(response),
            )
        if protocol_error is not None:
            raise protocol_error
        return result

    @staticmethod
    def _validate_page(
        page: object,
        *,
        expected_number: int,
        previous_totals: tuple[int, int] | None,
        endpoint_code: str,
        expected_size: int,
        max_pages: int,
    ) -> tuple[int, int]:
        number = getattr(page, "number")
        total_pages = getattr(page, "totalPages")
        total_elements = getattr(page, "totalElements")
        expected_pages = (
            (total_elements + expected_size - 1) // expected_size
            if total_elements
            else total_pages
        )
        if (
            number != expected_number
            or getattr(page, "size") != expected_size
            or total_pages < 0
            or total_elements < 0
            or total_pages > max_pages
            or total_elements > expected_size * max_pages
            or (total_elements > 0 and total_pages != expected_pages)
            or (total_elements > 0 and total_pages == 0)
            or (total_pages and number >= total_pages)
        ):
            raise GitFlicProtocolError(endpoint_code, 200)
        totals = (total_pages, total_elements)
        if previous_totals is not None and totals != previous_totals:
            raise GitFlicProtocolError(endpoint_code, 200)
        return totals

    @staticmethod
    def _validate_page_items(
        page: object, item_count: int, cumulative_count: int, endpoint_code: str
    ) -> None:
        size = getattr(page, "size")
        number = getattr(page, "number")
        total_pages = getattr(page, "totalPages")
        total_elements = getattr(page, "totalElements")
        expected_count = (
            size
            if number + 1 < total_pages
            else total_elements - size * max(total_pages - 1, 0)
        )
        if (
            item_count > size
            or cumulative_count > total_elements
            or (total_elements and item_count != expected_count)
        ):
            raise GitFlicProtocolError(endpoint_code, 200)

    async def list_open_mrs_strict(
        self, owner: str, project: str
    ) -> tuple[GitFlicMergeRequest, ...]:
        path = f"/project/{quote(owner, safe='')}/{quote(project, safe='')}/merge-request/list"
        items: list[GitFlicMergeRequest] = []
        totals: tuple[int, int] | None = None
        raw_count = 0
        raw_ids: set[int] = set()
        for page_number in range(10):
            response = await self._get(path, params={"page": page_number, "size": 100})
            page = self._validate_response(response, GitFlicMergeRequestsPage, "merge_requests")
            assert isinstance(page, GitFlicMergeRequestsPage)
            totals = self._validate_page(
                page.page, expected_number=page_number,
                previous_totals=totals, endpoint_code="merge_requests",
                expected_size=100, max_pages=10,
            )
            raw = page.embedded.mergeRequestModelList
            raw_count += len(raw)
            self._validate_page_items(page.page, len(raw), raw_count, "merge_requests")
            if raw_count > page.page.totalElements or raw_count > 1000:
                raise GitFlicProtocolError("merge_requests", response.status_code)
            for item in raw:
                if item.localId in raw_ids:
                    raise GitFlicProtocolError("merge_requests", response.status_code)
                raw_ids.add(item.localId)
            if any(item.status is None for item in raw):
                raise GitFlicProtocolError("merge_requests", response.status_code)
            items.extend(item for item in raw if item.status and item.status.id == "OPENED")
            if page_number + 1 >= page.page.totalPages:
                if raw_count != page.page.totalElements:
                    raise GitFlicProtocolError("merge_requests", response.status_code)
                break
        else:
            raise GitFlicProtocolError("merge_requests", 200)
        identifiers = [item.localId for item in items]
        if len(identifiers) != len(set(identifiers)):
            raise GitFlicProtocolError("merge_requests", 200)
        return tuple(items)

    async def get_discussions_strict(
        self, owner: str, project: str, merge_request_id: int
    ) -> tuple[GitFlicDiscussion, ...]:
        path = f"{self._mr_path(owner, project, merge_request_id)}/discussions"
        results: list[GitFlicDiscussion] = []
        totals: tuple[int, int] | None = None
        raw_count = 0
        for page_number in range(5):
            response = await self._get(path, params={"page": page_number, "size": 100})
            page = self._validate_response(response, GitFlicDiscussionsPage, "discussions")
            assert isinstance(page, GitFlicDiscussionsPage)
            totals = self._validate_page(
                page.page, expected_number=page_number,
                previous_totals=totals, endpoint_code="discussions",
                expected_size=100, max_pages=5,
            )
            envelopes = page.embedded.restDiscussionModelList
            raw_count += len(envelopes)
            self._validate_page_items(page.page, len(envelopes), raw_count, "discussions")
            if raw_count > page.page.totalElements or raw_count > 500:
                raise GitFlicProtocolError("discussions", response.status_code)
            results.extend(
                GitFlicDiscussion(**item.rootNote.model_dump(), replies=item.replies)
                for item in envelopes
            )
            if page_number + 1 >= page.page.totalPages:
                if raw_count != page.page.totalElements:
                    raise GitFlicProtocolError("discussions", response.status_code)
                return tuple(results)
        raise GitFlicProtocolError("discussions", 200)

    async def list_branch_protections(
        self, owner: str, project: str
    ) -> tuple[GitFlicBranchProtection, ...]:
        path = f"/project/{quote(owner, safe='')}/{quote(project, safe='')}/branch-protection"
        results: list[GitFlicBranchProtection] = []
        totals: tuple[int, int] | None = None
        raw_count = 0
        for page_number in range(10):
            response = await self._get(path, params={"page": page_number, "size": 100})
            page = self._validate_response(response, GitFlicBranchProtectionsPage, "branch_protections")
            assert isinstance(page, GitFlicBranchProtectionsPage)
            totals = self._validate_page(
                page.page, expected_number=page_number,
                previous_totals=totals, endpoint_code="branch_protections",
                expected_size=100, max_pages=10,
            )
            raw = page.embedded.branchProtectionApiModelList
            raw_count += len(raw)
            self._validate_page_items(page.page, len(raw), raw_count, "branch_protections")
            if raw_count > 1000:
                raise GitFlicProtocolError("branch_protections", response.status_code)
            results.extend(raw)
            if page_number + 1 >= page.page.totalPages:
                if len(results) != page.page.totalElements:
                    raise GitFlicProtocolError("branch_protections", response.status_code)
                keys = [(item.branchTemplate, item.priority) for item in results]
                if len(keys) != len(set(keys)):
                    raise GitFlicProtocolError("branch_protections", response.status_code)
                return tuple(results)
        raise GitFlicProtocolError("branch_protections", 200)

    async def start_pipeline(
        self,
        owner: str,
        project: str,
        request: GitFlicPipelineStartRequest,
    ) -> GitFlicPipelineStartResponse:
        path = f"/project/{quote(owner, safe='')}/{quote(project, safe='')}/cicd/pipeline/start"
        response = await self._post(
            path, json=request.model_dump(), allow_fallback=False
        )
        result = self._validate_response(response, GitFlicPipelineStartResponse, "pipeline_start")
        assert isinstance(result, GitFlicPipelineStartResponse)
        try:
            result.display_id
        except ValueError as error:
            raise GitFlicProtocolError(
                "pipeline_start", response.status_code,
                request_id=self._safe_request_id(response),
            ) from error
        return result

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
