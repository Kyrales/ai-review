import pytest

from ai_review.clients.gitflic.schema import GitFlicAuthor, GitFlicBranch, GitFlicChange, GitFlicChangeLine, GitFlicMergeRequest
from ai_review.services.vcs.gitflic.adapter import find_position, to_review_info


def make_change(*, new_path: str = "src/cf/sppr/a.bsl", old_path: str | None = None) -> GitFlicChange:
    return GitFlicChange(
        id="change-1",
        newPath=new_path,
        oldPath=old_path or new_path,
        changeType="MODIFIED",
        headers=[],
        lines=[
            GitFlicChangeLine(body="added", addLineNumber=12, op="add", type="line"),
            GitFlicChangeLine(body="removed", removeLineNumber=12, op="delete", type="line"),
            GitFlicChangeLine(body="context", addLineNumber=13, removeLineNumber=13, op="context", type="line"),
        ],
    )


def test_find_position_for_added_line_contains_all_location_fields():
    position = find_position([make_change()], "src/cf/sppr/a.bsl", 12)

    assert position.model_dump() == {
        "newLine": 12,
        "oldLine": 0,
        "newPath": "src/cf/sppr/a.bsl",
        "oldPath": "src/cf/sppr/a.bsl",
        "message": "",
    }


def test_find_position_preserves_old_line_for_replaced_line():
    change = make_change()
    change.lines[0] = GitFlicChangeLine(
        body="replacement",
        addLineNumber=12,
        removeLineNumber=11,
        op="replace_add",
        type="line",
    )

    position = find_position([change], "src/cf/sppr/a.bsl", 12)

    assert position.oldLine == 11


def test_find_position_rejects_deleted_and_context_lines():
    change = make_change()

    assert find_position([change], "src/cf/sppr/a.bsl", 13) is None
    assert find_position([change], "src/cf/sppr/a.bsl", 99) is None


def test_find_position_uses_old_path_for_renamed_file():
    position = find_position(
        [make_change(new_path="new.bsl", old_path="old.bsl")],
        "new.bsl",
        12,
    )

    assert position.newPath == "new.bsl"
    assert position.oldPath == "old.bsl"


def test_review_info_uses_trusted_environment_shas(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_REVIEW_BASE_SHA", "base-from-control")
    monkeypatch.setenv("AI_REVIEW_HEAD_SHA", "head-from-control")
    mr = GitFlicMergeRequest(
        id="mr-uuid",
        localId=41,
        title="MR",
        sourceBranch=GitFlicBranch(id="feature", title="feature", hash="untrusted-source"),
        targetBranch=GitFlicBranch(id="main", title="main", hash="untrusted-target"),
        createdBy=GitFlicAuthor(id="author"),
    )

    result = to_review_info(mr, ["src/cf/sppr/a.bsl"])

    assert result.base_sha == "base-from-control"
    assert result.head_sha == "head-from-control"
    assert result.changed_files == ["src/cf/sppr/a.bsl"]
