import os

from ai_review.clients.gitflic.schema import (
    GitFlicChange,
    GitFlicCreateDiscussion,
    GitFlicMergeRequest,
    GitFlicNote,
)
from ai_review.services.vcs.types import (
    BranchRefSchema,
    ReviewCommentSchema,
    ReviewInfoSchema,
    UserSchema,
)


def find_position(
    changes: list[GitFlicChange], file: str, line: int
) -> GitFlicCreateDiscussion | None:
    for change in changes:
        if change.newPath != file:
            continue
        previous_old_line: int | None = None
        for item in change.lines:
            if item.op.lower() in {"add", "replace_add"} and item.addLineNumber == line:
                return GitFlicCreateDiscussion(
                    newLine=line,
                    oldLine=item.removeLineNumber or previous_old_line or line,
                    newPath=change.newPath,
                    oldPath=change.oldPath,
                    message="",
                )
            if item.removeLineNumber is not None:
                previous_old_line = item.removeLineNumber
        if change.changeType.upper() == "ADD" and 1 <= line <= change.addedLinesCount:
            return GitFlicCreateDiscussion(
                newLine=line,
                oldLine=line,
                newPath=change.newPath,
                oldPath=change.oldPath,
                message="",
            )
    return None


def to_user(author_id: str, username: str, name: str) -> UserSchema:
    return UserSchema(id=author_id, username=username, name=name)


def to_review_info(mr: GitFlicMergeRequest, changed_files: list[str]) -> ReviewInfoSchema:
    return ReviewInfoSchema(
        id=mr.localId,
        title=mr.title,
        description=mr.description or "",
        author=to_user(mr.createdBy.id, mr.createdBy.username, mr.createdBy.fullName),
        source_branch=BranchRefSchema(ref=mr.sourceBranch.id, sha=mr.sourceBranch.hash),
        target_branch=BranchRefSchema(ref=mr.targetBranch.id, sha=mr.targetBranch.hash),
        base_sha=os.environ["AI_REVIEW_BASE_SHA"],
        head_sha=os.environ["AI_REVIEW_HEAD_SHA"],
        changed_files=changed_files,
    )


def to_review_comment(
    note: GitFlicNote, thread_id: str, position: GitFlicNote | None = None
) -> ReviewCommentSchema:
    location = position or note
    return ReviewCommentSchema(
        id=note.uuid,
        body=note.rawMessage,
        file=location.newPath,
        line=location.newLine,
        author=to_user(note.author.id, note.author.username, note.author.fullName),
        parent_id=note.discussionUuid,
        thread_id=thread_id,
    )
