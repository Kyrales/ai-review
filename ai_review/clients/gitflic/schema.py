from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GitFlicModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")


class GitFlicAuthor(GitFlicModel):
    id: str
    username: str = ""
    fullName: str = ""


class GitFlicBranch(GitFlicModel):
    id: str
    title: str
    hash: str


class GitFlicMergeRequest(GitFlicModel):
    id: str
    localId: int
    title: str
    description: str | None = None
    sourceBranch: GitFlicBranch
    targetBranch: GitFlicBranch
    createdBy: GitFlicAuthor


class GitFlicChangeLine(GitFlicModel):
    id: str | None = None
    body: str
    addLineNumber: int | None = None
    removeLineNumber: int | None = None
    op: str
    type: str


class GitFlicChange(GitFlicModel):
    id: str
    newPath: str
    oldPath: str
    changeType: str
    headers: list[str] = Field(default_factory=list)
    lines: list[GitFlicChangeLine] = Field(default_factory=list)
    addedLinesCount: int = 0
    removedLinesCount: int = 0


class GitFlicPage(GitFlicModel):
    size: int
    totalElements: int
    totalPages: int
    number: int


class GitFlicChanges(GitFlicModel):
    commitBlobs: list[GitFlicChange]
    totalAddedLines: int = 0
    totalRemovedLines: int = 0
    page: GitFlicPage


class GitFlicNote(GitFlicModel):
    uuid: str
    discussionUuid: str | None = None
    rawMessage: str = ""
    resolved: bool = False
    newPath: str | None = None
    oldPath: str | None = None
    newLine: int | None = None
    oldLine: int | None = None
    author: GitFlicAuthor
    createdAt: datetime


class GitFlicDiscussion(GitFlicNote):
    replies: list[GitFlicNote] = Field(default_factory=list)


class GitFlicCreateDiscussion(GitFlicModel):
    newLine: int | None = None
    oldLine: int | None = None
    newPath: str | None = None
    oldPath: str | None = None
    message: str


class GitFlicReply(GitFlicModel):
    discussionUuid: str
    message: str


class GitFlicDiscussionEnvelope(GitFlicModel):
    rootNote: GitFlicNote
    replies: list[GitFlicNote] = Field(default_factory=list)


class GitFlicDiscussionsEmbedded(GitFlicModel):
    restDiscussionModelList: list[GitFlicDiscussionEnvelope]


class GitFlicDiscussionsPage(GitFlicModel):
    embedded: GitFlicDiscussionsEmbedded = Field(
        default_factory=GitFlicDiscussionsEmbedded,
        alias="_embedded",
    )
    page: GitFlicPage
