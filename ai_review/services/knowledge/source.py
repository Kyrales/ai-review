from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from collections.abc import Sequence

from ai_review.services.knowledge.block import parse_knowledge_block
from ai_review.services.knowledge.schema import KnowledgeCandidate, normalize_knowledge_text
from ai_review.services.vcs.markers import MarkerKind, parse_marker
from ai_review.services.vcs.types import (
    KnowledgeReviewSourceProtocol,
    ReviewSummarySchema,
    ReviewThreadSchema,
)


@dataclass(frozen=True)
class ReviewKnowledgeScan:
    review: ReviewSummarySchema
    block_count: int = 0
    rule_count: int = 0
    error: str | None = None


class KnowledgeSourceService:
    """Validate untrusted review replies before they reach the compiler."""

    def __init__(
        self,
        source: KnowledgeReviewSourceProtocol,
        trusted_ai_id: str | int,
        trusted_reviewers: Sequence[str],
        max_rules_per_reply: int = 3,
    ) -> None:
        self.source = source
        self.trusted_ai_id = trusted_ai_id
        self.trusted_reviewers = {name.casefold() for name in trusted_reviewers}
        self.max_rules_per_reply = max_rules_per_reply

    async def discover(self) -> list[ReviewKnowledgeScan]:
        rows: list[ReviewKnowledgeScan] = []
        for review in await self.source.list_open_reviews():
            try:
                threads = await self.source.get_review_threads(review.id)
                blocks, candidates = self._validated(threads, review.id)
            except Exception as error:
                rows.append(ReviewKnowledgeScan(review=review, error=str(error)))
                continue
            if candidates:
                rows.append(
                    ReviewKnowledgeScan(
                        review=review,
                        block_count=blocks,
                        rule_count=len(candidates),
                    )
                )
        return rows

    async def candidates_for(
        self,
        review_id: str | int,
        threads: Sequence[ReviewThreadSchema] | None = None,
    ) -> tuple[KnowledgeCandidate, ...]:
        if threads is None:
            threads = await self.source.get_review_threads(review_id)
        return self._validated(threads, review_id)[1]

    def _validated(
        self,
        threads: Sequence[ReviewThreadSchema],
        review_id: str | int,
    ) -> tuple[int, tuple[KnowledgeCandidate, ...]]:
        comments = [comment for thread in threads for comment in thread.comments]
        by_id: dict[str, list] = {}
        for comment in comments:
            by_id.setdefault(str(comment.id), []).append(comment)

        accepted: list[KnowledgeCandidate] = []
        block_count = 0
        seen: set[tuple[str, str, str, str]] = set()
        for comment in comments:
            marker = parse_marker(comment.body, comment.author.id, self.trusted_ai_id)
            candidates = parse_knowledge_block(comment.body)
            if marker is None or marker.kind is not MarkerKind.FOLLOWUP or candidates is None:
                continue
            if any(count > self.max_rules_per_reply for count in Counter(
                item.source_reply_id for item in candidates
            ).values()):
                continue

            block: list[KnowledgeCandidate] = []
            valid = True
            for item in candidates:
                matches = by_id.get(item.source_reply_id, [])
                if len(matches) != 1 or item.source_reply_id not in marker.covered:
                    valid = False
                    break
                source = matches[0]
                if source.author.username.casefold() not in self.trusted_reviewers:
                    valid = False
                    break
                if normalize_knowledge_text(item.source_quote) not in normalize_knowledge_text(source.body):
                    valid = False
                    break
                key = (
                    str(self.source.provider),
                    self.source.project_key,
                    str(review_id),
                    item.source_reply_id,
                )
                if key not in seen:
                    block.append(item)
            if not valid:
                continue
            if block:
                block_count += 1
            for item in block:
                key = (
                    str(self.source.provider), self.source.project_key,
                    str(review_id), item.source_reply_id,
                )
                seen.add(key)
                accepted.append(item)
        return block_count, tuple(accepted)
