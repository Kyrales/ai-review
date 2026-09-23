import unicodedata
from uuid import UUID

from ai_review.services.knowledge.block import parse_knowledge_block
from ai_review.services.vcs.markers import MarkerKind, ReviewMarker, parse_marker
from ai_review.services.vcs.types import ReviewCommentSchema, ReviewThreadSchema


class FollowupStateAnalyzer:
    """Pure state analysis shared by follow-up execution and dispatch."""

    def __init__(self, trusted_author_id: str):
        self.trusted_author_id = trusted_author_id

    @staticmethod
    def canonical_id(value: object) -> str:
        normalized = unicodedata.normalize("NFC", str(value))
        try:
            return str(UUID(normalized))
        except ValueError:
            return normalized

    def marker(self, comment: ReviewCommentSchema) -> ReviewMarker | None:
        return parse_marker(comment.body, comment.author.id, self.trusted_author_id)

    def pending_comments(
        self,
        thread: ReviewThreadSchema,
        related_threads: tuple[ReviewThreadSchema, ...] = (),
    ) -> tuple[ReviewCommentSchema, ...]:
        covered: set[str] = set()
        human: list[ReviewCommentSchema] = []
        for comment in thread.comments:
            marker = self.marker(comment)
            if marker and marker.kind is MarkerKind.FOLLOWUP:
                covered.update(self.canonical_id(item) for item in marker.covered)
            elif comment.parent_id is not None:
                human.append(comment)
        origin = self.canonical_id(thread.id)
        for related in related_threads:
            for comment in related.comments:
                marker = self.marker(comment)
                if (
                    marker is not None
                    and marker.kind is MarkerKind.FOLLOWUP
                    and marker.version == "v2"
                    and self.canonical_id(marker.origin) == origin
                ):
                    covered.update(self.canonical_id(item) for item in marker.covered)
        return tuple(
            comment
            for comment in human
            if self.canonical_id(comment.id) not in covered
        )[:50]

    def pending_ids(
        self,
        thread: ReviewThreadSchema,
        related_threads: tuple[ReviewThreadSchema, ...] = (),
    ) -> tuple[str, ...]:
        return tuple(
            self.canonical_id(comment.id)
            for comment in self.pending_comments(thread, related_threads)
        )

    def continuations(
        self,
        thread: ReviewThreadSchema,
        related_threads: tuple[ReviewThreadSchema, ...],
    ) -> tuple[ReviewThreadSchema, ...]:
        origin = self.canonical_id(thread.id)
        return tuple(
            related
            for related in related_threads
            if related.id != thread.id
            and any(
                (marker := self.marker(comment)) is not None
                and marker.kind is MarkerKind.FOLLOWUP
                and marker.version == "v2"
                and self.canonical_id(marker.origin) == origin
                for comment in related.comments
            )
        )

    def last_followup_requires_resolve(self, thread: ReviewThreadSchema) -> bool:
        for comment in reversed(thread.comments):
            marker = self.marker(comment)
            if marker and marker.kind is MarkerKind.FOLLOWUP:
                return (
                    marker.verdict in {"fixed", "withdrawn"}
                    or parse_knowledge_block(comment.body) is not None
                )
        return False

    def resolve_thread_ids(
        self,
        thread: ReviewThreadSchema,
        related_threads: tuple[ReviewThreadSchema, ...] = (),
    ) -> tuple[str, ...]:
        identifiers = [
            self.canonical_id(item.id)
            for item in self.continuations(thread, related_threads)
            if item.resolved is not True
        ]
        if thread.resolved is not True and self.last_followup_requires_resolve(thread):
            identifiers.append(self.canonical_id(thread.id))
        return tuple(identifiers)
