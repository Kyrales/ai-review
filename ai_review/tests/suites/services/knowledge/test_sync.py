from pathlib import Path

import pytest

from ai_review.libs.constants.vcs_provider import VCSProvider
from ai_review.services.knowledge.compiler import CompilationResult, CompilerDecision, CompilerOperation
from ai_review.services.knowledge.schema import KnowledgeCandidate
from ai_review.services.knowledge.sync import KnowledgeSyncService, NoKnowledgeError, ReviewClosedError
from ai_review.services.vcs.types import ReviewSummarySchema


def candidate(text: str = "Правило") -> KnowledgeCandidate:
    return KnowledgeCandidate(source_reply_id="r1", source_quote="цитата", type="new_check", rule=text, rationale="причина")


class FakeKnowledgeSource:
    provider = VCSProvider.GITLAB
    project_key = "portable/project"

    def __init__(self, state="open"):
        self.state = state
        self.calls = []

    async def get_review(self, review_id):
        self.calls.append(("review", str(review_id)))
        return ReviewSummarySchema(id=str(review_id), source_branch="feature", state=self.state)

    async def get_review_threads(self, review_id):
        self.calls.append(("threads", str(review_id)))
        return []


class VerifiedSource:
    def __init__(self, source):
        self.source = source

    async def candidates_for(self, review_id, threads=None):
        return (candidate(f"Правило {review_id}"),)


class FakeCompiler:
    async def compile(self, manual_context, existing_rules, candidates):
        decisions = tuple(
            CompilerDecision(source_input_indexes=[i], action="add", target_managed_index=None,
                             result_rule=item.rule, related_indexes=[], reason="новое")
            for i, item in enumerate(candidates)
        )
        operations = tuple(
            CompilerOperation(operation="add", source_input_indexes=[i], target_managed_index=None, result_rule=item.rule)
            for i, item in enumerate(candidates)
        )
        return CompilationResult(decisions=decisions, operations=operations, conflicts=(), rules=tuple(existing_rules) + tuple(c.rule for c in candidates))


@pytest.mark.asyncio
async def test_preview_rechecks_open_state_reads_threads_and_returns_full_transaction(tmp_path: Path):
    """Catches stale selection, skipped discussion refresh or incomplete write preview."""
    rules = tmp_path / "project-rules.md"
    rules.write_text("# Rules\n", encoding="utf-8")
    raw = FakeKnowledgeSource()
    service = KnowledgeSyncService(VerifiedSource(raw), FakeCompiler(), rules)

    preview = await service.preview(["52", "49"])

    assert raw.calls == [("review", "52"), ("threads", "52"), ("review", "49"), ("threads", "49")]
    assert [d.action for d in preview.decisions] == ["add", "add"]
    assert preview.counts == {"add": 2, "merge": 0, "duplicate": 0, "conflict": 0, "reject": 0}
    assert "Правило 52" in preview.diff and "Правило 49" in preview.diff
    assert preview.expected_sha256 and preview.new_bytes != rules.read_bytes()
    assert service.commit(preview) is True
    assert "Правило 52" in rules.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_preview_rejects_review_closed_after_selection(tmp_path: Path):
    """Catches compiling knowledge from an MR closed after the table was shown."""
    rules = tmp_path / "rules.md"; rules.write_text("# Rules\n", encoding="utf-8")
    raw = FakeKnowledgeSource(state="closed")
    with pytest.raises(ReviewClosedError, match="52"):
        await KnowledgeSyncService(VerifiedSource(raw), FakeCompiler(), rules).preview(["52"])
    assert raw.calls == [("review", "52")]


@pytest.mark.asyncio
async def test_preview_does_not_call_compiler_when_knowledge_disappeared(tmp_path: Path):
    """Catches an unnecessary LLM call after the repeated read finds no knowledge."""
    rules = tmp_path / "rules.md"; rules.write_text("# Rules\n", encoding="utf-8")
    raw = FakeKnowledgeSource()
    verified = VerifiedSource(raw)
    async def none(*_args, **_kwargs): return ()
    verified.candidates_for = none
    with pytest.raises(NoKnowledgeError):
        await KnowledgeSyncService(verified, FakeCompiler(), rules).preview(["52"])




@pytest.mark.asyncio
@pytest.mark.parametrize("folder,rules_name", [("one", "rules.md"), ("two", "nested/project.md")])
async def test_same_service_is_portable_between_repository_layouts(tmp_path: Path, folder: str, rules_name: str):
    """Catches project-specific owner, root or rules-path assumptions in the orchestrator."""
    root = tmp_path / folder; path = root / rules_name; path.parent.mkdir(parents=True); path.write_text("# R\n", encoding="utf-8")
    raw = FakeKnowledgeSource()
    preview = await KnowledgeSyncService(VerifiedSource(raw), FakeCompiler(), path).preview(["1"])
    assert str(path) in preview.diff
