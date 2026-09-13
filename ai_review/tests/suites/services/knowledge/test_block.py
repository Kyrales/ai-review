import json

import pytest

from ai_review.services.knowledge.block import parse_knowledge_block, render_knowledge_block
from ai_review.services.knowledge.json import StrictJSONError, loads_strict
from ai_review.services.knowledge.schema import KnowledgeCandidate


def candidate(reply_id: str = "reply-1") -> KnowledgeCandidate:
    return KnowledgeCandidate.model_validate(
        {
            "source_reply_id": reply_id,
            "source_quote": "Следует проверять метаданные",
            "type": "new_check",
            "rule": "Проверять метаданные до публикации замечания.",
            "rationale": "Проверка предотвращает ложные замечания.",
        }
    )


def test_rendered_block_is_deterministic_and_round_trips_inside_reply() -> None:
    expected = (
        '#ai-review-knowledge\n\n```json\n{\n  "rules": [\n    {\n'
        '      "source_reply_id": "reply-1",\n'
        '      "source_quote": "Следует проверять метаданные",\n'
        '      "type": "new_check",\n'
        '      "rule": "Проверять метаданные до публикации замечания.",\n'
        '      "rationale": "Проверка предотвращает ложные замечания."\n'
        "    }\n  ]\n}\n```"
    )

    rendered = render_knowledge_block((candidate(),))

    assert rendered == expected
    assert parse_knowledge_block(f"Ответ AI\n\n{rendered}\n\n<!-- marker -->") == (candidate(),)


@pytest.mark.parametrize(
    "payload",
    [
        '{"rules": [], "RULES": []}',
        '{"rules": [{"source_reply_id":"1","source_reply_id":"2"}]}',
        '{"rules": [], "unknown": true}',
        '{"rules": []} trailing',
        '{"rules": [NaN]}',
        '{"rules": [Infinity]}',
        json.dumps({"rules": [[[[[[[[[[[[[[[[[[]]]]]]]]]]]]]]]]]]}),
    ],
)
def test_strict_json_rejects_duplicate_unknown_trailing_non_finite_and_deep_data(
    payload: str,
) -> None:
    if "unknown" in payload:
        assert parse_knowledge_block(f"#ai-review-knowledge\n\n```json\n{payload}\n```") is None
    else:
        with pytest.raises(StrictJSONError):
            loads_strict(payload)


def test_parser_rejects_entire_block_when_one_item_is_invalid() -> None:
    valid = candidate().model_dump(by_alias=True)
    invalid = {**valid, "source_reply_id": "other", "rule": ""}
    payload = json.dumps({"rules": [valid, invalid]}, ensure_ascii=False)

    assert parse_knowledge_block(f"#ai-review-knowledge\n\n```json\n{payload}\n```") is None


def test_parser_rejects_duplicate_tag_or_wrong_layout() -> None:
    rendered = render_knowledge_block((candidate(),))

    assert parse_knowledge_block(f"{rendered}\n{rendered}") is None
    assert parse_knowledge_block(rendered.replace("\n\n```json", "\n```json")) is None


def test_parser_accepts_reply_with_an_additional_fenced_block() -> None:
    rendered = render_knowledge_block((candidate(),))

    assert parse_knowledge_block(f"```bsl\nСообщить();\n```\n\n{rendered}") == (candidate(),)


def test_parser_accepts_additional_fenced_block_inside_blockquote() -> None:
    rendered = render_knowledge_block((candidate(),))

    quoted_fence = "> ```bsl\n> Сообщить();\n> ```"
    assert parse_knowledge_block(f"{quoted_fence}\n\n{rendered}") == (candidate(),)


@pytest.mark.parametrize(
    ("opening", "closing"),
    [(">  ```bsl", ">  ```"), (">   > ```bsl", ">   > ```")],
)
def test_parser_accepts_fence_after_commonmark_blockquote_spacing(
    opening: str,
    closing: str,
) -> None:
    rendered = render_knowledge_block((candidate(),))
    quoted_fence = f"{opening}\n> Код\n{closing}"

    assert parse_knowledge_block(f"{quoted_fence}\n\n{rendered}") == (candidate(),)


def test_backticks_inside_json_string_remain_valid() -> None:
    value = candidate().model_copy(
        update={"rule": "Использовать пример:\n```bsl\nСообщить();\n```"}
    )

    rendered = render_knowledge_block((value,))

    assert parse_knowledge_block(rendered) == (value,)


def test_parser_rejects_more_than_150_rules_and_more_than_three_per_reply() -> None:
    too_many = [candidate(f"reply-{index}").model_dump(by_alias=True) for index in range(151)]
    four_same = [candidate().model_dump(by_alias=True) for _ in range(4)]

    for rules in (too_many, four_same):
        payload = json.dumps({"rules": rules}, ensure_ascii=False)
        assert parse_knowledge_block(f"#ai-review-knowledge\n\n```json\n{payload}\n```") is None
