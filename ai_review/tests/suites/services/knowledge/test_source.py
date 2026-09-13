import pytest

from ai_review.libs.constants.vcs_provider import VCSProvider
from ai_review.services.knowledge.block import render_knowledge_block
from ai_review.services.knowledge.schema import KnowledgeCandidate
from ai_review.services.knowledge.source import KnowledgeSourceService
from ai_review.services.vcs.markers import MarkerKind, ReviewMarker, decorate_ai_message
from ai_review.services.vcs.types import (
    ReviewCommentSchema,
    ReviewSummarySchema,
    ReviewThreadSchema,
    ThreadKind,
    UserSchema,
)


def _candidate(source_id: str = "reply-1", quote: str = "Используйте БезопасныйРежим") -> KnowledgeCandidate:
    return KnowledgeCandidate(
        source_reply_id=source_id,
        source_quote=quote,
        type="correction",
        rule="Используйте безопасный режим при внешних вызовах",
        rationale="Так требует проект",
    )


def _thread(*, ai_id: str = "ai-1", covered: tuple[str, ...] = ("reply-1",),
            block: str | None = None, human_username: str = "chief",
            quote: str = "Используйте БезопасныйРежим",
            candidate_quote: str | None = None) -> ReviewThreadSchema:
    marker = ReviewMarker(
        version="v2", kind=MarkerKind.FOLLOWUP, head="a" * 40,
        covered=covered, verdict="fixed", origin="thread-1", publication="b" * 64,
    )
    body = decorate_ai_message(
        block or render_knowledge_block((_candidate(quote=candidate_quote or quote),)), marker
    )
    return ReviewThreadSchema(
        id="thread-1", kind=ThreadKind.INLINE,
        comments=[
            ReviewCommentSchema(id="finding", body="Проблема", author=UserSchema(id=ai_id)),
            ReviewCommentSchema(id="reply-1", body=quote, author=UserSchema(id="u-1", username=human_username)),
            ReviewCommentSchema(id="followup", body=body, author=UserSchema(id=ai_id)),
        ],
    )


class FakeSource:
    provider = VCSProvider.GITFLIC
    project_key = "team/project"

    def __init__(self, reviews, threads):
        self.reviews = reviews
        self.threads = threads

    async def list_open_reviews(self):
        return self.reviews

    async def get_review(self, review_id):
        return next(item for item in self.reviews if str(item.id) == str(review_id))

    async def get_review_threads(self, review_id):
        value = self.threads[str(review_id)]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.asyncio
async def test_discover_returns_only_eligible_reviews_and_keeps_per_review_errors():
    """Catches an empty/error MR being shown as eligible or hiding other MRs."""
    reviews = [
        ReviewSummarySchema(id="1", source_branch="feature/good", state="open"),
        ReviewSummarySchema(id="2", source_branch="feature/empty", state="open"),
        ReviewSummarySchema(id="3", source_branch="feature/error", state="open"),
    ]
    service = KnowledgeSourceService(
        FakeSource(reviews, {"1": [_thread()], "2": [], "3": RuntimeError("API unavailable")}),
        trusted_ai_id="ai-1", trusted_reviewers=("chief",),
    )

    rows = await service.discover()

    assert [(row.review.id, row.block_count, row.rule_count, row.error) for row in rows] == [
        ("1", 1, 1, None),
        ("3", 0, 0, "API unavailable"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "thread",
    [
        _thread(ai_id="other-ai"),
        _thread(covered=("other",)),
        _thread(human_username="stranger"),
        _thread(candidate_quote="Совсем другой текст"),
        ReviewThreadSchema(
            id="thread-1", kind=ThreadKind.INLINE,
            comments=[ReviewCommentSchema(id="x", body="#ai-review-knowledge", author=UserSchema(id="u"))],
        ),
    ],
)
async def test_rejects_forged_or_unverifiable_knowledge_blocks(thread):
    """Catches trust-boundary bypasses for author, marker, source, allowlist and quote."""
    source = FakeSource([ReviewSummarySchema(id="1", source_branch="x", state="open")], {"1": [thread]})
    assert await KnowledgeSourceService(source, "ai-1", ("chief",)).candidates_for("1") == ()


@pytest.mark.asyncio
async def test_rejects_whole_block_when_one_item_is_invalid_and_deduplicates_source():
    """Catches partial acceptance and duplicate provenance in one run."""
    good = _candidate()
    bad = _candidate(source_id="missing")
    damaged = render_knowledge_block((good, bad))
    combined = _thread(block=damaged)
    combined.comments.append(_thread().comments[-1])
    source = FakeSource(
        [ReviewSummarySchema(id="1", source_branch="x", state="open")],
        {"1": [combined]},
    )
    service = KnowledgeSourceService(source, "ai-1", ("CHIEF",))
    assert await service.candidates_for("1") == (good,)


@pytest.mark.asyncio
async def test_configured_per_reply_limit_rejects_whole_oversized_block():
    """Catches sync accepting more rules per reply than the project configured."""
    block = render_knowledge_block((_candidate(), _candidate()))
    source = FakeSource(
        [ReviewSummarySchema(id="1", source_branch="x", state="open")],
        {"1": [_thread(block=block)]},
    )
    service = KnowledgeSourceService(source, "ai-1", ("chief",), max_rules_per_reply=1)
    assert await service.candidates_for("1") == ()
