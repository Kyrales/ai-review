import json
import logging

import pytest

from ai_review.services.knowledge.extractor import KnowledgeExtractor
from ai_review.services.knowledge.schema import EligibleKnowledgeSource, KnowledgeExtractionContext


class FakeGateway:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str]] = []

    async def ask(self, prompt: str, prompt_system: str) -> str:
        self.calls.append((prompt, prompt_system))
        return self.response


def source(reply_id: str, text: str = "Следует проверять метаданные") -> EligibleKnowledgeSource:
    return EligibleKnowledgeSource(reply_id=reply_id, username="Reviewer", text=text)


def item(reply_id: str, quote: str = "Следует проверять метаданные") -> dict[str, str]:
    return {
        "source_reply_id": reply_id,
        "source_quote": quote,
        "type": "new_check",
        "rule": "Проверять метаданные до публикации замечания.",
        "rationale": "Это предотвращает ложные замечания.",
    }


CONTEXT = KnowledgeExtractionContext(
    original_finding="AI указал на отсутствие проверки.",
    current_code="Если Проверка Тогда Возврат; КонецЕсли;",
    current_diff="@@ -1 +1 @@",
)


@pytest.mark.asyncio
async def test_extractor_makes_one_separate_call_with_only_labelled_sources() -> None:
    gateway = FakeGateway('{"knowledge": []}')
    extractor = KnowledgeExtractor(gateway)

    assert await extractor.extract(CONTEXT, (source("reply-1"),), 3) == ()

    assert len(gateway.calls) == 1
    prompt, system = gateway.calls[0]
    assert "eligible_knowledge_sources" in prompt
    assert '"reply_id": "reply-1"' in prompt
    assert '"max_rules_per_reply": 3' in prompt
    assert '"original_finding": "AI указал на отсутствие проверки."' in prompt
    assert '"current_code": "Если Проверка Тогда Возврат; КонецЕсли;"' in prompt
    assert '"current_diff": "@@ -1 +1 @@"' in prompt
    assert "только чётко сформулированное переиспользуемое правило" in system
    assert "AI-ревьювер" in system


@pytest.mark.asyncio
async def test_extractor_validates_exact_normalized_quote_and_discards_bad_items() -> None:
    good = item("reply-1", "Café\nправило")
    bad_id = item("missing")
    bad_quote = item("reply-1", "не существующая цитата")
    gateway = FakeGateway(json.dumps({"knowledge": [good, bad_id, bad_quote]}, ensure_ascii=False))
    extractor = KnowledgeExtractor(gateway)

    result = await extractor.extract(CONTEXT, (source("reply-1", "Café\r\nправило целиком"),), 3)

    assert tuple(value.source_reply_id for value in result) == ("reply-1",)


@pytest.mark.asyncio
async def test_limit_plus_one_discards_only_offending_reply_group(caplog: pytest.LogCaptureFixture) -> None:
    rules = [item("reply-1") for _ in range(4)] + [item("reply-2")]
    gateway = FakeGateway(json.dumps({"knowledge": rules,}, ensure_ascii=False))
    extractor = KnowledgeExtractor(gateway)

    with caplog.at_level(logging.WARNING, logger="ai_review.knowledge.extractor"):
        result = await extractor.extract(CONTEXT, (source("reply-1"), source("reply-2")), 3)

    assert tuple(value.source_reply_id for value in result) == ("reply-2",)
    assert "reply-1" in caplog.text
    assert "4" in caplog.text
    assert "3" in caplog.text


@pytest.mark.asyncio
async def test_invalid_envelope_or_item_does_not_raise() -> None:
    invalid_outputs = (
        '{"knowledge": [], "KNOWLEDGE": []}',
        '{"knowledge": [{"source_reply_id": "reply-1"}]}',
        '{"knowledge": [NaN]}',
    )

    for output in invalid_outputs:
        extractor = KnowledgeExtractor(FakeGateway(output))
        assert await extractor.extract(CONTEXT, (source("reply-1"),), 3) == ()


@pytest.mark.asyncio
async def test_extractor_rejects_invalid_limit_without_calling_llm() -> None:
    gateway = FakeGateway('{"knowledge": []}')
    extractor = KnowledgeExtractor(gateway)

    with pytest.raises(ValueError):
        await extractor.extract(CONTEXT, (source("reply-1"),), 4)

    assert gateway.calls == []


@pytest.mark.asyncio
async def test_extractor_limits_total_to_150_items() -> None:
    sources = tuple(source(f"reply-{index}") for index in range(51))
    rules = [item(value.reply_id) for value in sources for _ in range(3)]
    rules.append(item("reply-0"))
    extractor = KnowledgeExtractor(FakeGateway(json.dumps({"knowledge": rules})))

    assert await extractor.extract(CONTEXT, sources, 3) == ()
