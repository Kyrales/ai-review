from __future__ import annotations

import json
from collections.abc import Sequence
from importlib import resources
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from ai_review.services.knowledge.json import StrictJSONError, loads_strict
from ai_review.services.knowledge.rules_file import validate_rule
from ai_review.services.knowledge.schema import KnowledgeCandidate


VerifiedKnowledgeCandidate = KnowledgeCandidate
_Text = Annotated[str, StringConstraints(min_length=1, max_length=4000)]


class CompilationContractError(ValueError):
    pass


class CompilerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source_input_indexes: Annotated[list[int], Field(min_length=1)]
    action: Literal["add", "merge", "duplicate", "conflict", "reject"]
    target_managed_index: int | None
    result_rule: Annotated[str, StringConstraints(min_length=1, max_length=2000)] | None
    related_indexes: list[int]
    reason: _Text


class CompilerOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    operation: Literal["add", "replace"]
    source_input_indexes: Annotated[list[int], Field(min_length=1)]
    target_managed_index: int | None
    result_rule: Annotated[str, StringConstraints(min_length=1, max_length=2000)]


class _CompilerEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    decisions: list[CompilerDecision]


class CompilationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decisions: tuple[CompilerDecision, ...]
    operations: tuple[CompilerOperation, ...]
    conflicts: tuple[CompilerDecision, ...]
    rules: tuple[str, ...]


class KnowledgeCompilerLLMGatewayProtocol(Protocol):
    async def ask(
        self, instructions: str, input_text: str, max_output_tokens: int
    ) -> str: ...


class KnowledgeCompiler:
    def __init__(self, llm_gateway: KnowledgeCompilerLLMGatewayProtocol) -> None:
        self.llm_gateway = llm_gateway

    async def compile(
        self,
        manual_context: str,
        existing_rules: Sequence[str],
        candidates: Sequence[VerifiedKnowledgeCandidate],
    ) -> CompilationResult:
        prompt = json.dumps(
            {
                "manual_context": manual_context,
                "existing_rules": [
                    {"managed_index": index, "rule": rule}
                    for index, rule in enumerate(existing_rules)
                ],
                "candidates": [
                    {"input_index": index, **candidate.model_dump(by_alias=True)}
                    for index, candidate in enumerate(candidates)
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        prompt_system = resources.files("ai_review.prompts").joinpath(
            "default_knowledge_compiler.md"
        ).read_text(encoding="utf-8")
        raw = await self.llm_gateway.ask(prompt_system, prompt, 20_000)
        envelope = self._parse(raw)
        operations = self._validate(envelope.decisions, len(existing_rules), len(candidates))
        decisions = tuple(
            sorted(envelope.decisions, key=lambda item: min(item.source_input_indexes))
        )

        replacements = {
            operation.target_managed_index: operation.result_rule
            for operation in operations
            if operation.operation == "replace"
        }
        rules = tuple(
            replacements.get(index, rule) for index, rule in enumerate(existing_rules)
        ) + tuple(
            operation.result_rule for operation in operations if operation.operation == "add"
        )
        return CompilationResult(
            decisions=decisions,
            operations=operations,
            conflicts=tuple(item for item in decisions if item.action == "conflict"),
            rules=rules,
        )

    @staticmethod
    def _parse(raw: str) -> _CompilerEnvelope:
        try:
            return _CompilerEnvelope.model_validate(loads_strict(raw))
        except (StrictJSONError, ValidationError) as error:
            raise CompilationContractError("invalid compiler response") from error

    @staticmethod
    def _validate(
        decisions: Sequence[CompilerDecision], managed_count: int, input_count: int
    ) -> tuple[CompilerOperation, ...]:
        covered = [index for item in decisions for index in item.source_input_indexes]
        if sorted(covered) != list(range(input_count)):
            raise CompilationContractError("decisions must cover every input exactly once")

        by_input = {
            index: item for item in decisions for index in item.source_input_indexes
        }
        replacement_targets: set[int] = set()
        operations: list[CompilerOperation] = []
        for item in decisions:
            _validate_indexes(item.related_indexes, input_count, "related_indexes")
            if set(item.source_input_indexes) & set(item.related_indexes):
                raise CompilationContractError("related_indexes overlap decision sources")

            target = item.target_managed_index
            if target is not None and not 0 <= target < managed_count:
                raise CompilationContractError("unknown target_managed_index")

            if item.action in {"add", "merge"}:
                if item.related_indexes:
                    raise CompilationContractError(
                        "add and merge decisions cannot have related_indexes"
                    )
                if item.action == "add" and target is not None:
                    raise CompilationContractError("add decision cannot target a managed rule")
                if item.action == "merge" and target is None:
                    raise CompilationContractError("merge decision requires a managed target")
                if item.result_rule is None:
                    raise CompilationContractError(
                        "add and merge decisions require result_rule"
                    )
                try:
                    validate_rule(item.result_rule)
                except ValueError as error:
                    raise CompilationContractError("invalid result_rule") from error
                if item.action == "merge":
                    if target in replacement_targets:
                        raise CompilationContractError(
                            "managed target is replaced more than once"
                        )
                    replacement_targets.add(target)
                operations.append(
                    CompilerOperation(
                        operation="add" if item.action == "add" else "replace",
                        source_input_indexes=sorted(item.source_input_indexes),
                        target_managed_index=target,
                        result_rule=item.result_rule,
                    )
                )
            elif item.result_rule is not None:
                raise CompilationContractError(
                    "duplicate, conflict and reject decisions cannot produce rules"
                )
            elif item.action == "reject" and (target is not None or item.related_indexes):
                raise CompilationContractError(
                    "reject decision cannot target or relate to another rule"
                )

        _validate_conflict_symmetry(decisions, by_input)
        replacements = sorted(
            (item for item in operations if item.operation == "replace"),
            key=lambda item: item.target_managed_index,
        )
        additions = sorted(
            (item for item in operations if item.operation == "add"),
            key=lambda item: min(item.source_input_indexes),
        )
        return tuple(replacements + additions)


def _validate_indexes(indexes: Sequence[int], upper_bound: int, field: str) -> None:
    if len(set(indexes)) != len(indexes):
        raise CompilationContractError(f"{field} contains duplicate indexes")
    if any(index < 0 or index >= upper_bound for index in indexes):
        raise CompilationContractError(f"{field} contains an unknown index")


def _validate_conflict_symmetry(
    decisions: Sequence[CompilerDecision], by_input: dict[int, CompilerDecision]
) -> None:
    relations: dict[int, set[int]] = {}
    for item in decisions:
        if item.action != "conflict":
            continue
        sources = set(item.source_input_indexes)
        for index in sources:
            relations[index] = (sources - {index}) | set(item.related_indexes)

    for index, related in relations.items():
        for other in related:
            if by_input[other].action != "conflict" or index not in relations.get(other, set()):
                raise CompilationContractError("new input conflict is not symmetric")
