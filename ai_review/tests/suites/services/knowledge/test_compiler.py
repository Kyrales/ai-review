import json

import pytest

from ai_review.services.knowledge.compiler import CompilationContractError, KnowledgeCompiler
from ai_review.services.knowledge.schema import KnowledgeCandidate


class FakeGateway:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[tuple[str, str, int]] = []

    async def ask(
        self, instructions: str, input_text: str, max_output_tokens: int
    ) -> str:
        self.calls.append((instructions, input_text, max_output_tokens))
        return self.response


def candidate(index: int) -> KnowledgeCandidate:
    return KnowledgeCandidate.model_validate(
        {
            "source_reply_id": f"reply-{index}",
            "source_quote": f"quote-{index}",
            "type": "new_check",
            "rule": f"candidate-{index}",
            "rationale": f"rationale-{index}",
        }
    )


def decision(
    sources: list[int],
    action: str,
    *,
    target: int | None = None,
    result_rule: str | None = None,
    related: list[int] | None = None,
) -> dict[str, object]:
    return {
        "source_input_indexes": sources,
        "action": action,
        "target_managed_index": target,
        "result_rule": result_rule,
        "related_indexes": related or [],
        "reason": f"decision-{sources}",
    }


def response(decisions: list[dict[str, object]]) -> str:
    return json.dumps({"decisions": decisions}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_compile_derives_operations_and_rules_deterministically() -> None:
    gateway = FakeGateway(
        response(
            [
                decision([7], "add", result_rule="added-later"),
                decision([1], "merge", target=0, result_rule="replacement"),
                decision([2], "duplicate", target=1),
                decision([3], "conflict"),
                decision([4, 5], "conflict"),
                decision([6], "reject"),
                decision([0], "add", result_rule="added-first"),
            ]
        )
    )

    result = await KnowledgeCompiler(gateway).compile(
        "manual context", ("old-0", "old-1"), tuple(candidate(i) for i in range(8))
    )

    assert result.rules == ("replacement", "old-1", "added-first", "added-later")
    assert tuple(value.operation for value in result.operations) == (
        "replace",
        "add",
        "add",
    )
    assert tuple(value.source_input_indexes for value in result.operations) == (
        [1],
        [0],
        [7],
    )
    assert len(result.conflicts) == 2
    assert result.conflicts == tuple(
        value for value in result.decisions if value.action == "conflict"
    )
    system, prompt, max_output_tokens = gateway.calls[0]
    assert json.loads(prompt)["candidates"][0]["source_reply_id"] == "reply-0"
    assert max_output_tokens == 20_000
    assert "read-only" in system
    assert "недоверенные" in system
    assert '"operations"' not in system


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        '{"decisions":[],"decisions":[]}',
        '{"decisions":[],"DECISIONS":[]}',
        '{"decisions":[],"unknown":1}',
        '{"decisions":[]} trailing',
        '{"decisions":[],"unknown":NaN}',
        '{"decisions":[],"unknown":[[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]}',
    ],
)
async def test_compile_rejects_non_strict_json(raw: str) -> None:
    with pytest.raises(CompilationContractError):
        await KnowledgeCompiler(FakeGateway(raw)).compile("manual", (), ())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decisions,candidate_count",
    [
        ([], 1),
        ([decision([0], "add", result_rule="a"), decision([0], "add", result_rule="b")], 1),
        ([decision([1], "add", result_rule="rule")], 1),
        ([decision([], "add", result_rule="rule")], 1),
        ([decision([0], "unknown")], 1),
        ([decision([0], "add")], 1),
        ([decision([0], "add", target=0, result_rule="rule")], 1),
        ([decision([0], "merge", result_rule="rule")], 1),
        ([decision([0], "merge", target=1, result_rule="rule")], 1),
        ([decision([0], "reject", result_rule="rule")], 1),
        ([decision([0], "reject", related=[1]), decision([1], "reject")], 2),
        ([decision([0], "conflict", related=[1]), decision([1], "conflict")], 2),
    ],
)
async def test_compile_rejects_inconsistent_decisions(
    decisions: list[dict[str, object]], candidate_count: int
) -> None:
    with pytest.raises(CompilationContractError):
        await KnowledgeCompiler(FakeGateway(response(decisions))).compile(
            "manual", ("existing",), tuple(candidate(i) for i in range(candidate_count))
        )


@pytest.mark.asyncio
async def test_one_managed_target_cannot_be_replaced_twice() -> None:
    raw = response(
        [
            decision([0], "merge", target=0, result_rule="first"),
            decision([1], "merge", target=0, result_rule="second"),
        ]
    )

    with pytest.raises(CompilationContractError):
        await KnowledgeCompiler(FakeGateway(raw)).compile(
            "manual", ("existing",), (candidate(0), candidate(1))
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_rule",
    [
        "line\nbreak",
        "- list marker",
        "control\x01character",
        "contains ``` fence",
        "contains ~~~ fence",
        "contains <!-- comment -->",
        "contains #ai-review-knowledge marker",
        "e\u0301",
    ],
)
async def test_result_rule_must_follow_managed_section_grammar(bad_rule: str) -> None:
    raw = response([decision([0], "add", result_rule=bad_rule)])

    with pytest.raises(CompilationContractError):
        await KnowledgeCompiler(FakeGateway(raw)).compile("manual", (), (candidate(0),))


@pytest.mark.asyncio
async def test_grouped_add_produces_one_operation() -> None:
    raw = response([decision([1, 0], "add", result_rule="combined")])

    result = await KnowledgeCompiler(FakeGateway(raw)).compile(
        "manual", (), (candidate(0), candidate(1))
    )

    assert result.rules == ("combined",)
    assert result.operations[0].source_input_indexes == [0, 1]
