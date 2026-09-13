import json
import logging
from collections import Counter
from collections.abc import Sequence
from importlib import resources
from typing import Protocol

from pydantic import ValidationError

from ai_review.services.knowledge.json import StrictJSONError, loads_strict
from ai_review.services.knowledge.schema import (
    EligibleKnowledgeSource,
    KnowledgeCandidate,
    KnowledgeExtractionContext,
)

logger = logging.getLogger("ai_review.knowledge.extractor")


class KnowledgeLLMGatewayProtocol(Protocol):
    async def ask(self, prompt: str, prompt_system: str) -> str: ...


class KnowledgeExtractor:
    def __init__(self, llm_gateway: KnowledgeLLMGatewayProtocol) -> None:
        self.llm_gateway = llm_gateway

    async def extract(
        self,
        context: KnowledgeExtractionContext,
        sources: Sequence[EligibleKnowledgeSource],
        max_rules_per_reply: int,
    ) -> tuple[KnowledgeCandidate, ...]:
        if not 1 <= max_rules_per_reply <= 3:
            raise ValueError("max_rules_per_reply must be between 1 and 3")
        if not sources:
            return ()

        prompt = json.dumps(
            {
                "context": context.model_dump(),
                "max_rules_per_reply": max_rules_per_reply,
                "eligible_knowledge_sources": [
                    source.model_dump() for source in sources
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        prompt_system = resources.files("ai_review.prompts").joinpath(
            "default_knowledge_extractor.md"
        ).read_text(encoding="utf-8")
        raw = await self.llm_gateway.ask(prompt, prompt_system)
        candidates = self._parse_candidates(raw)
        if not candidates:
            return ()

        source_counts = Counter(source.reply_id for source in sources)
        source_by_id = {
            source.reply_id: source
            for source in sources
            if source_counts[source.reply_id] == 1
        }
        verified = tuple(
            candidate
            for candidate in candidates
            if self._is_verified(candidate, source_by_id)
        )
        counts = Counter(candidate.source_reply_id for candidate in verified)
        exceeded = {
            reply_id for reply_id, count in counts.items()
            if count > max_rules_per_reply
        }
        for reply_id in sorted(exceeded):
            logger.warning(
                "Discarded all %d knowledge candidates for reply %s: limit is %d",
                counts[reply_id],
                reply_id,
                max_rules_per_reply,
            )
        return tuple(
            candidate
            for candidate in verified
            if candidate.source_reply_id not in exceeded
        )

    @staticmethod
    def _parse_candidates(raw: str) -> tuple[KnowledgeCandidate, ...]:
        try:
            root = loads_strict(raw)
        except StrictJSONError as error:
            logger.warning("Discarded invalid knowledge JSON: %s", error)
            return ()
        if not isinstance(root, dict) or set(root) != {"knowledge"}:
            logger.warning("Discarded invalid knowledge envelope")
            return ()
        items = root["knowledge"]
        if not isinstance(items, list) or len(items) > 150:
            logger.warning("Discarded knowledge envelope with invalid item count")
            return ()

        result: list[KnowledgeCandidate] = []
        for index, item in enumerate(items):
            try:
                result.append(KnowledgeCandidate.model_validate(item))
            except ValidationError as error:
                logger.warning("Discarded invalid knowledge item %d: %s", index, error)
        return tuple(result)

    @staticmethod
    def _is_verified(
        candidate: KnowledgeCandidate,
        source_by_id: dict[str, EligibleKnowledgeSource],
    ) -> bool:
        source = source_by_id.get(candidate.source_reply_id)
        if source is None:
            logger.warning(
                "Discarded knowledge candidate with unknown or ambiguous source %s",
                candidate.source_reply_id,
            )
            return False
        if candidate.source_quote not in source.text:
            logger.warning(
                "Discarded knowledge candidate with inexact quote for source %s",
                candidate.source_reply_id,
            )
            return False
        return True
