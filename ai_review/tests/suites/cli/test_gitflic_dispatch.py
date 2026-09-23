import json

from typer.testing import CliRunner

from ai_review.cli.main import app


runner = CliRunner()


def test_gitflic_dispatch_help_exposes_no_secrets() -> None:
    result = runner.invoke(app, ["gitflic-dispatch", "--help"])

    assert result.exit_code == 0
    assert all(name in result.output for name in ("--all", "--merge-request-id", "--control-ref"))
    assert "token" not in result.output.casefold()


def test_gitflic_dispatch_passes_deduplicated_ids(monkeypatch) -> None:
    captured = {}

    async def fake(**values):
        captured.update(values)
        print(json.dumps({"status": "success"}))
        return 0

    monkeypatch.setattr(
        "ai_review.cli.commands.gitflic_dispatch.run_gitflic_dispatch_command", fake
    )
    result = runner.invoke(app, [
        "gitflic-dispatch", "--owner", "rt-vt", "--project", "sppr",
        "--control-ref", "ai-review-control",
        "--merge-request-id", "7", "--merge-request-id", "7",
        "--merge-request-id", "9",
    ])

    assert result.exit_code == 0
    assert captured["merge_request_ids"] == (7, 7, 9)


def test_gitflic_dispatch_rejects_missing_or_conflicting_selection() -> None:
    common = [
        "gitflic-dispatch", "--owner", "rt-vt", "--project", "sppr",
        "--control-ref", "ai-review-control",
    ]

    missing = runner.invoke(app, common)
    conflicting = runner.invoke(app, [*common, "--all", "--merge-request-id", "7"])

    assert missing.exit_code == 2
    assert conflicting.exit_code == 2
