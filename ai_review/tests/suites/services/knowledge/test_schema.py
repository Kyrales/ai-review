import pytest
from pydantic import ValidationError

from ai_review.services.knowledge.schema import (
    EligibleKnowledgeSource,
    KnowledgeCandidate,
    KnowledgeExtractionContext,
)


def test_candidate_accepts_supported_types_and_alias() -> None:
    for knowledge_type in ("false_positive", "correction", "new_check"):
        candidate = KnowledgeCandidate.model_validate(
            {
                "source_reply_id": "reply-1",
                "source_quote": "Нужно проверять метаданные",
                "type": knowledge_type,
                "rule": "Проверять метаданные до формирования замечания.",
                "rationale": "Так исключаются ложные срабатывания.",
            }
        )
        assert candidate.knowledge_type == knowledge_type
        assert candidate.model_dump(by_alias=True)["type"] == knowledge_type


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_reply_id", "x" * 257),
        ("source_quote", "x" * 1001),
        ("rule", "x" * 2001),
        ("rationale", "x" * 4001),
    ],
)
def test_candidate_rejects_each_oversized_field(field: str, value: str) -> None:
    data = {
        "source_reply_id": "reply-1",
        "source_quote": "цитата",
        "type": "correction",
        "rule": "Правило",
        "rationale": "Причина",
    }
    data[field] = value

    with pytest.raises(ValidationError):
        KnowledgeCandidate.model_validate(data)


def test_candidate_rejects_unknown_fields() -> None:
    data = {
        "source_reply_id": "reply-1",
        "source_quote": "цитата",
        "type": "correction",
        "rule": "Правило",
        "rationale": "Причина",
        "extra": True,
    }

    with pytest.raises(ValidationError):
        KnowledgeCandidate.model_validate(data)


def test_candidate_accepts_fenced_text_because_json_escaping_keeps_block_safe() -> None:
    candidate = KnowledgeCandidate.model_validate(
        {
            "source_reply_id": "reply-1",
            "source_quote": "пример ```bsl",
            "type": "correction",
            "rule": "Использовать пример:\n```bsl\nКод\n```",
            "rationale": "Ревьювер привёл ``` пример.",
        }
    )

    assert "```" in candidate.rule


def test_candidate_accepts_only_external_type_alias() -> None:
    data = {
        "source_reply_id": "reply-1",
        "source_quote": "цитата",
        "knowledge_type": "correction",
        "rule": "Правило",
        "rationale": "Причина",
    }

    with pytest.raises(ValidationError):
        KnowledgeCandidate.model_validate(data)


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (EligibleKnowledgeSource, {"reply_id": " ", "username": "Reviewer", "text": "Правило"}),
        (EligibleKnowledgeSource, {"reply_id": "1", "username": "\t", "text": "Правило"}),
        (EligibleKnowledgeSource, {"reply_id": "1", "username": "Reviewer", "text": "\n"}),
        (
            KnowledgeCandidate,
            {
                "source_reply_id": "1",
                "source_quote": " ",
                "type": "correction",
                "rule": "Правило",
                "rationale": "Причина",
            },
        ),
        (
            KnowledgeCandidate,
            {
                "source_reply_id": "1",
                "source_quote": "цитата",
                "type": "correction",
                "rule": "\t",
                "rationale": "Причина",
            },
        ),
    ],
)
def test_models_reject_whitespace_only_required_strings(model: type, data: dict) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(data)


def test_extraction_context_requires_finding_and_carries_code_and_diff() -> None:
    context = KnowledgeExtractionContext(
        original_finding="  Ошибка проверки  ",
        current_code="Процедура Тест()\nКонецПроцедуры",
        current_diff="@@ -1 +1 @@",
    )

    assert context.original_finding == "Ошибка проверки"
    assert context.current_code.startswith("Процедура")

    with pytest.raises(ValidationError):
        KnowledgeExtractionContext(
            original_finding=" ",
            current_code="",
            current_diff="",
        )


def test_eligible_source_normalizes_nfc_and_line_endings_only() -> None:
    source = EligibleKnowledgeSource(
        reply_id="reply-1",
        username="Reviewer",
        text="Cafe\u0301\r\n  правило\rс пробелами  ",
    )

    assert source.text == "Café\n  правило\nс пробелами  "
