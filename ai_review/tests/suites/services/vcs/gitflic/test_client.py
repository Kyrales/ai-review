from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ai_review.clients.gitflic.schema import (
    GitFlicAuthor,
    GitFlicBranch,
    GitFlicChange,
    GitFlicChangeLine,
    GitFlicDiscussion,
    GitFlicMergeRequest,
    GitFlicNote,
)
from ai_review.services.vcs.gitflic.client import GitFlicVCSClient
from ai_review.services.vcs.types import ThreadKind


def note(
    uuid: str,
    *,
    path: str | None = None,
    line: int | None = None,
    old_path: str | None = None,
    old_line: int | None = None,
) -> GitFlicNote:
    return GitFlicNote(
        uuid=uuid,
        rawMessage=f"message {uuid}",
        newPath=path,
        oldPath=old_path if old_path is not None else path,
        newLine=line,
        oldLine=old_line if old_line is not None else line,
        author=GitFlicAuthor(id="author", username="dev", fullName="Developer"),
        createdAt=datetime(2026, 1, 1),
    )


@pytest.fixture
def gitflic_http(monkeypatch: pytest.MonkeyPatch):
    client = SimpleNamespace(
        get_mr=AsyncMock(
            return_value=GitFlicMergeRequest(
                id="mr",
                localId=41,
                title="Title",
                sourceBranch=GitFlicBranch(
                    id="feature", title="feature", hash="source"
                ),
                targetBranch=GitFlicBranch(id="main", title="main", hash="target"),
                createdBy=GitFlicAuthor(
                    id="author", username="dev", fullName="Developer"
                ),
            )
        ),
        get_changes=AsyncMock(
            return_value=SimpleNamespace(
                commitBlobs=[
                    GitFlicChange(
                        id="change",
                        newPath="a.bsl",
                        oldPath="a.bsl",
                        changeType="MODIFIED",
                        headers=[],
                        lines=[
                            GitFlicChangeLine(
                                body="added", addLineNumber=7, op="add", type="line"
                            )
                        ],
                    )
                ]
            )
        ),
        get_discussions=AsyncMock(
            return_value=[
                GitFlicDiscussion(
                    **note("inline", path="a.bsl", line=7).model_dump(),
                    replies=[note("reply")],
                ),
                GitFlicDiscussion(**note("general").model_dump(), replies=[]),
            ]
        ),
        create_discussion=AsyncMock(
            side_effect=lambda _owner, _project, _mr, request: note(
                "created",
                path=request.newPath,
                line=request.newLine,
                old_path=request.oldPath,
                old_line=request.oldLine,
            )
        ),
        reply=AsyncMock(),
        delete=AsyncMock(),
        resolve=AsyncMock(),
    )
    monkeypatch.setattr(
        "ai_review.services.vcs.gitflic.client.get_gitflic_http_client", lambda: client
    )
    monkeypatch.setattr(
        "ai_review.services.vcs.gitflic.client.settings.vcs",
        SimpleNamespace(
            pipeline=SimpleNamespace(owner="rt-vt", project="sppr", merge_request_id=41)
        ),
    )
    monkeypatch.setenv("AI_REVIEW_BASE_SHA", "base")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", "head")
    return client


@pytest.mark.asyncio
async def test_client_maps_review_and_threads(gitflic_http):
    client = GitFlicVCSClient()

    info = await client.get_review_info()
    inline_threads = await client.get_inline_threads()
    general_threads = await client.get_general_threads()

    assert (info.id, info.base_sha, info.head_sha, info.changed_files) == (
        41,
        "base",
        "head",
        ["a.bsl"],
    )
    assert [
        (thread.kind, thread.id, len(thread.comments)) for thread in inline_threads
    ] == [
        (ThreadKind.INLINE, "inline", 2),
    ]
    assert [(thread.kind, thread.id) for thread in general_threads] == [
        (ThreadKind.SUMMARY, "general")
    ]


@pytest.mark.asyncio
async def test_client_posts_general_inline_and_replies(gitflic_http):
    client = GitFlicVCSClient()

    await client.create_general_comment("general")
    await client.create_inline_comment("a.bsl", 7, "inline")
    await client.create_inline_reply("inline", "reply")
    await client.delete_inline_comment("inline")

    requests = [
        call.kwargs["request"]
        for call in gitflic_http.create_discussion.await_args_list
    ]
    assert [request.message for request in requests] == ["general", "inline"]
    assert requests[0].newPath is None
    assert (requests[1].newPath, requests[1].newLine) == ("a.bsl", 7)
    gitflic_http.reply.assert_awaited_once_with("rt-vt", "sppr", 41, "inline", "reply")
    gitflic_http.delete.assert_awaited_once_with("rt-vt", "sppr", 41, "inline")


@pytest.mark.asyncio
async def test_client_reuses_changes_for_multiple_inline_comments(gitflic_http):
    client = GitFlicVCSClient()

    await client.create_inline_comment("a.bsl", 7, "first")
    await client.create_inline_comment("a.bsl", 7, "second")

    gitflic_http.get_changes.assert_awaited_once_with("rt-vt", "sppr", 41)


@pytest.mark.asyncio
async def test_client_raises_when_inline_position_is_unknown(gitflic_http):
    gitflic_http.get_changes.return_value.commitBlobs[0].lines = []

    with pytest.raises(RuntimeError, match=r"a\.bsl:8"):
        await GitFlicVCSClient().create_inline_comment("a.bsl", 8, "finding")

    gitflic_http.create_discussion.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_deletes_unbound_inline_response(gitflic_http):
    gitflic_http.create_discussion.side_effect = None
    gitflic_http.create_discussion.return_value = note("unexpected-general")

    with pytest.raises(RuntimeError, match="without an inline position"):
        await GitFlicVCSClient().create_inline_comment("a.bsl", 7, "finding")

    gitflic_http.delete.assert_awaited_once_with(
        "rt-vt", "sppr", 41, "unexpected-general"
    )
