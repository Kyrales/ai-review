from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Sequence

from ai_review.services.knowledge.compiler import CompilerDecision, KnowledgeCompiler
from ai_review.services.knowledge.rules_file import RulesDocument, RulesFileTransaction
from ai_review.services.knowledge.source import KnowledgeSourceService


class ReviewClosedError(RuntimeError):
    pass


class NoKnowledgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class SyncPreview:
    decisions: tuple[CompilerDecision, ...]
    conflicts: tuple[CompilerDecision, ...]
    counts: dict[str, int]
    diff: str
    expected_sha256: str
    new_bytes: bytes


class KnowledgeSyncService:
    def __init__(self, source: KnowledgeSourceService, compiler: KnowledgeCompiler, rules_path: Path) -> None:
        self.source = source
        self.compiler = compiler
        self.rules_path = rules_path

    async def discover(self):
        return await self.source.discover()

    async def preview(self, selected_ids: Sequence[str | int]) -> SyncPreview:
        candidates = []
        seen = set()
        for review_id in selected_ids:
            review = await self.source.source.get_review(review_id)
            if review.state != "open":
                raise ReviewClosedError(f"merge request {review_id} is no longer open")
            threads = await self.source.source.get_review_threads(review_id)
            for item in await self.source.candidates_for(review_id, threads):
                key = (
                    str(self.source.source.provider), self.source.source.project_key,
                    str(review_id), item.source_reply_id,
                )
                if key not in seen:
                    seen.add(key)
                    candidates.append(item)
        if not candidates:
            raise NoKnowledgeError("selected merge requests no longer contain valid knowledge")

        document = RulesDocument.read(self.rules_path)
        result = await self.compiler.compile(
            document.manual_context_text(), document.rules, candidates
        )
        new_bytes = document.with_rules(result.rules)
        counts = Counter(item.action for item in result.decisions)
        return SyncPreview(
            decisions=result.decisions,
            conflicts=result.conflicts,
            counts={name: counts[name] for name in ("add", "merge", "duplicate", "conflict", "reject")},
            diff=document.unified_diff(new_bytes),
            expected_sha256=document.sha256,
            new_bytes=new_bytes,
        )

    def commit(self, preview: SyncPreview) -> bool:
        return RulesFileTransaction(self.rules_path).commit(
            preview.expected_sha256, preview.new_bytes
        )
