from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError

from ai_review.clients.gitflic.client import GitFlicHTTPClient, GitFlicHTTPClientError
from ai_review.clients.gitflic.schema import GitFlicCreateDiscussion
from ai_review.libs.constants.vcs_provider import VCSProvider


AUTHOR = {"id": "user-1", "username": "reviewer", "fullName": "Reviewer"}


def note(uuid: str, **values: object) -> dict[str, object]:
    return {
        "uuid": uuid,
        "rawMessage": "message",
        "resolved": False,
        "author": AUTHOR,
        "createdAt": "2026-09-06T10:00:00Z",
        **values,
    }


def test_provider_enum_contains_gitflic() -> None:
    assert VCSProvider("GITFLIC") is VCSProvider.GITFLIC


def test_note_schema_rejects_wrong_field_types() -> None:
    invalid = note(
        "d1",
        newLine="41",
        createdAt=datetime(2026, 9, 6, 10, tzinfo=timezone.utc),
    )

    with pytest.raises(ValidationError):
        from ai_review.clients.gitflic.schema import GitFlicNote

        GitFlicNote.model_validate(invalid)


def test_create_discussion_allows_general_comment_without_position() -> None:
    request = GitFlicCreateDiscussion(message="General comment")

    assert request.model_dump(exclude_none=True) == {"message": "General comment"}


@pytest.mark.parametrize(
    "position",
    [
        {"newLine": 12, "newPath": "new.py", "oldPath": "old.py"},
        {"oldLine": 11, "newPath": "new.py", "oldPath": "old.py"},
    ],
)
def test_create_discussion_rejects_partial_position(position: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GitFlicCreateDiscussion(message="Please fix", **position)


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        ("GitFlicChanges", {"totalAddedLines": 0, "totalRemovedLines": 0, "page": {"size": 1, "totalElements": 0, "totalPages": 1, "number": 0}}),
        ("GitFlicDiscussionsPage", {"_embedded": {}, "page": {"size": 1, "totalElements": 0, "totalPages": 1, "number": 0}}),
        ("GitFlicChange", {"id": "c", "newPath": "new.py", "oldPath": "old.py", "changeType": "MODIFY"}),
        ("GitFlicDiscussion", note("d1")),
    ],
)
def test_required_response_collections_fail_closed(schema: str, payload: dict[str, object]) -> None:
    from ai_review.clients.gitflic import schema as gitflic_schema

    with pytest.raises(ValidationError):
        getattr(gitflic_schema, schema).model_validate(payload)


@pytest.mark.asyncio
async def test_client_sends_token_only_in_authorization_header() -> None:
    captured: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured
        captured = request
        return httpx.Response(200, request=request, json={
            "id": "mr-1",
            "localId": 41,
            "title": "MR",
            "sourceBranch": {"id": "feature", "title": "feature", "hash": "head"},
            "targetBranch": {"id": "main", "title": "main", "hash": "base"},
            "createdBy": AUTHOR,
        })

    client = GitFlicHTTPClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_mr("rt-vt", "sppr", 41)
    finally:
        await client.aclose()

    assert result.localId == 41
    assert captured is not None
    assert captured.headers["Authorization"] == "token token"
    assert "token" not in str(captured.url)
    assert captured.url.path == "/project/rt-vt/sppr/merge-request/41"


@pytest.mark.asyncio
async def test_get_changes_reads_all_pages() -> None:
    requested_pages: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        requested_pages.append(page)
        return httpx.Response(200, request=request, json={
            "commitBlobs": [{
                "id": f"blob-{page + 1}",
                "newPath": f"new-{page + 1}.py",
                "oldPath": f"old-{page + 1}.py",
                "changeType": "MODIFY",
                "headers": [],
                "lines": [{
                    "body": f"+value = {page + 1}",
                    "addLineNumber": page + 1,
                    "removeLineNumber": None,
                    "op": "add",
                    "type": "line",
                }],
            }],
            "totalAddedLines": 2,
            "totalRemovedLines": 0,
            "page": {"size": 1, "totalElements": 2, "totalPages": 2, "number": page},
        })

    client = GitFlicHTTPClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_changes("rt-vt", "sppr", 41)
    finally:
        await client.aclose()

    assert [change.id for change in result.commitBlobs] == ["blob-1", "blob-2"]
    assert [change.lines[0].addLineNumber for change in result.commitBlobs] == [1, 2]
    assert requested_pages == [0, 1]


@pytest.mark.asyncio
async def test_get_discussions_reads_all_pages() -> None:
    requested_pages: list[int] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        requested_pages.append(page)
        item = {
            "rootNote": note(f"d{page + 1}"),
            "replies": [note(f"r{page + 1}", discussionUuid=f"d{page + 1}")],
        }
        return httpx.Response(200, request=request, json={
            "_embedded": {"restDiscussionModelList": [item]},
            "page": {"size": 1, "totalElements": 2, "totalPages": 2, "number": page},
        })

    client = GitFlicHTTPClient(transport=httpx.MockTransport(handler))
    try:
        result = await client.get_discussions("rt-vt", "sppr", 41)
    finally:
        await client.aclose()

    assert [item.uuid for item in result] == ["d1", "d2"]
    assert [item.replies[0].uuid for item in result] == ["r1", "r2"]
    assert requested_pages == [0, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 404])
async def test_get_discussions_does_not_hide_http_errors(status_code: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, request=request, text="denied")

    client = GitFlicHTTPClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(GitFlicHTTPClientError) as error:
            await client.get_discussions("rt-vt", "sppr", 41)
    finally:
        await client.aclose()

    assert error.value.status_code == status_code


@pytest.mark.asyncio
async def test_discussion_mutations_use_documented_endpoints_and_payloads() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "/delete/" in request.url.path:
            return httpx.Response(204, request=request)
        return httpx.Response(200, request=request, json=note(
            "created",
            resolved="/resolve/" in request.url.path,
        ))

    client = GitFlicHTTPClient(transport=httpx.MockTransport(handler))
    discussion = GitFlicCreateDiscussion(
        newLine=12,
        oldLine=11,
        newPath="new.py",
        oldPath="old.py",
        message="Please fix",
    )
    try:
        created = await client.create_discussion("rt-vt", "sppr", 41, discussion)
        replied = await client.reply("rt-vt", "sppr", 41, "discussion-1", "Fixed")
        resolved = await client.resolve("rt-vt", "sppr", 41, "discussion-1")
        deleted = await client.delete("rt-vt", "sppr", 41, "discussion-1")
    finally:
        await client.aclose()

    assert (created.uuid, replied.uuid, resolved.resolved, deleted) == ("created", "created", True, None)
    assert [request.url.path for request in requests] == [
        "/project/rt-vt/sppr/merge-request/41/discussions/create",
        "/project/rt-vt/sppr/merge-request/41/discussions/reply",
        "/project/rt-vt/sppr/merge-request/41/discussions/resolve/discussion-1",
        "/project/rt-vt/sppr/merge-request/41/discussions/delete/discussion-1",
    ]
    assert requests[0].content == (b'{"newLine":12,"oldLine":11,"newPath":"new.py",'
                                   b'"oldPath":"old.py","message":"Please fix"}')
    assert requests[1].content == b'{"discussionUuid":"discussion-1","message":"Fixed"}'
    assert datetime.fromisoformat("2026-09-06T10:00:00+00:00") == created.createdAt


@pytest.mark.asyncio
async def test_general_discussion_sends_only_message() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, request=request, json=note("general"))

    client = GitFlicHTTPClient(transport=httpx.MockTransport(handler))
    try:
        await client.create_discussion(
            "rt-vt", "sppr", 41, GitFlicCreateDiscussion(message="General comment")
        )
    finally:
        await client.aclose()

    assert requests[0].content == b'{"message":"General comment"}'
