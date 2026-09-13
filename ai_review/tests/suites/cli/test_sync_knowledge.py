from pathlib import Path

from typer.testing import CliRunner

from ai_review.cli.main import app
from ai_review.services.knowledge.source import ReviewKnowledgeScan
from ai_review.services.vcs.types import ReviewSummarySchema


runner = CliRunner()


def async_builder(value):
    async def build(**_):
        return value

    return build


class FakeRuntime:
    enabled = True

    def __init__(self):
        self.preview_ids = None
        self.committed = False

    async def discover(self):
        return [
            ReviewKnowledgeScan(ReviewSummarySchema(id="52", source_branch="fix/calculation", state="open"), 2, 3),
            ReviewKnowledgeScan(ReviewSummarySchema(id="49", source_branch="feature/access", state="open"), 1, 1),
        ]

    async def preview(self, ids):
        self.preview_ids = list(ids)
        return type("Preview", (), {
            "decisions": [type("D", (), {
                "source_input_indexes": [0, 2],
                "action": "merge",
                "target_managed_index": 1,
                "result_rule": "Проверять новое правило",
                "related_indexes": [],
                "reason": "уточняет существующее",
            })()],
            "conflicts": (), "counts": {"add": 1, "merge": 0, "duplicate": 0, "conflict": 0, "reject": 0},
            "diff": "--- rules\n+++ rules\n+Новое правило\n",
        })()

    def commit(self, preview):
        self.committed = True
        return True


def test_interactive_selection_accepts_comma_list_and_confirms(monkeypatch, tmp_path: Path):
    """Catches hidden ineligible choices, partial preview, or writing without confirmation."""
    runtime = FakeRuntime()
    monkeypatch.setattr("ai_review.cli.commands.sync_knowledge.build_runtime", async_builder(runtime))
    result = runner.invoke(app, ["sync-knowledge", "--repository-root", str(tmp_path)], input="52,49\ny\n")
    assert result.exit_code == 0
    assert "fix/calculation" in result.output and "feature/access" in result.output
    assert runtime.preview_ids == ["52", "49"]
    assert "Новое правило" in result.output and "add: 1" in result.output
    assert "inputs=[0, 2]" in result.output
    assert "action=merge" in result.output and "target=1" in result.output
    assert "result_rule=Проверять новое правило" in result.output
    assert "reason=уточняет существующее" in result.output
    assert runtime.committed is True


def test_all_and_yes_still_show_diff(monkeypatch, tmp_path: Path):
    """Catches --yes suppressing preview or --all selecting only one row."""
    runtime = FakeRuntime()
    monkeypatch.setattr("ai_review.cli.commands.sync_knowledge.build_runtime", async_builder(runtime))
    result = runner.invoke(app, ["sync-knowledge", "--repository-root", str(tmp_path), "--all", "--yes"])
    assert result.exit_code == 0
    assert runtime.preview_ids == ["52", "49"] and runtime.committed
    assert "+Новое правило" in result.output


def test_enter_cancels_and_invalid_or_unlisted_id_is_usage_error(monkeypatch, tmp_path: Path):
    """Catches accidental processing on Enter and bypass of the displayed allowlist."""
    runtime = FakeRuntime()
    monkeypatch.setattr("ai_review.cli.commands.sync_knowledge.build_runtime", async_builder(runtime))
    cancelled = runner.invoke(app, ["sync-knowledge", "--repository-root", str(tmp_path)], input="\n")
    invalid = runner.invoke(app, ["sync-knowledge", "--repository-root", str(tmp_path), "--merge-request-id", "999"])
    assert cancelled.exit_code == 0 and runtime.preview_ids is None
    assert invalid.exit_code == 2 and "999" in invalid.output


def test_disabled_exits_before_source_or_llm(monkeypatch, tmp_path: Path):
    """Catches side effects while the feature flag is disabled."""
    monkeypatch.setattr("ai_review.cli.commands.sync_knowledge.build_runtime", async_builder(None))
    result = runner.invoke(app, ["sync-knowledge", "--repository-root", str(tmp_path), "--all", "--yes"])
    assert result.exit_code == 0
    assert "выключ" in result.output.casefold()


def test_interactive_all_aliases(monkeypatch, tmp_path: Path):
    """Catches loss of either documented interactive all spelling."""
    for selection in ("all", "все"):
        runtime = FakeRuntime()
        monkeypatch.setattr("ai_review.cli.commands.sync_knowledge.build_runtime", async_builder(runtime))
        result = runner.invoke(app, ["sync-knowledge", "--repository-root", str(tmp_path), "--yes"], input=selection + "\n")
        assert result.exit_code == 0 and runtime.preview_ids == ["52", "49"]
