import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

from typer.testing import CliRunner

from ai_review.cli.main import app
from ai_review.services.dispatch.models import DispatchReport


runner = CliRunner()


def test_gitflic_dispatch_help_exposes_no_secrets() -> None:
    result = runner.invoke(app, ["gitflic-dispatch", "--help"])

    assert result.exit_code == 0
    help_text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.output)
    assert all(name in help_text for name in ("--all", "--merge-request-id", "--control-ref"))
    assert "token" not in help_text.casefold()


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


def test_gitflic_dispatch_invalid_url_and_ref_return_usage_json(monkeypatch) -> None:
    monkeypatch.setenv("AI_REVIEW_GITFLIC_TOKEN", "secret")
    monkeypatch.setenv(
        "AI_REVIEW_GITFLIC_USER_ID", "12345678-1234-4234-9234-123456789abc"
    )
    common = [
        "gitflic-dispatch", "--owner", "rt-vt", "--project", "sppr",
        "--merge-request-id", "7",
    ]

    bad_url = runner.invoke(app, [*common, "--control-ref", "ai-review-control", "--api-url", "https://[::1"])
    bad_ref = runner.invoke(app, [*common, "--control-ref", "../evil"])
    dot_owner = runner.invoke(app, [
        "gitflic-dispatch", "--owner", "..", "--project", "sppr",
        "--control-ref", "ai-review-control", "--merge-request-id", "7",
    ])

    assert bad_url.exit_code == 2
    assert bad_ref.exit_code == 2
    assert dot_owner.exit_code == 2
    assert json.loads(bad_url.output)["error_code"] == "invalid_configuration"
    assert json.loads(bad_ref.output)["error_code"] == "invalid_configuration"


def _report(status: str) -> DispatchReport:
    return DispatchReport(
        status=status,
        failure_scope="none" if status == "success" else ("item" if status == "partial" else "source"),
        error_code=None if status != "fatal" else "source_preflight_failed",
        selected_count=1, started_count=int(status == "success"),
        skipped_count=0, failed_count=int(status == "partial"), items=(),
    )


def test_gitflic_dispatch_runtime_author_lookup_and_exit_mapping(monkeypatch) -> None:
    monkeypatch.setenv("AI_REVIEW_GITFLIC_TOKEN", "secret")
    monkeypatch.delenv("AI_REVIEW_GITFLIC_USER_ID", raising=False)
    client = SimpleNamespace(
        get_authenticated_user=AsyncMock(
            return_value=SimpleNamespace(id="12345678-1234-4234-9234-123456789abc")
        ),
        aclose=AsyncMock(),
    )
    statuses = iter(("success", "partial", "fatal"))

    class FakeService:
        def __init__(self, *args):
            assert args[-1] == "12345678-1234-4234-9234-123456789abc"

        async def run(self, _selection):
            return _report(next(statuses))

    monkeypatch.setattr("ai_review.cli.commands.gitflic_dispatch.GitFlicHTTPClient", lambda **_: client)
    monkeypatch.setattr("ai_review.cli.commands.gitflic_dispatch.GitFlicDispatchService", FakeService)
    args = [
        "gitflic-dispatch", "--owner", "rt-vt", "--project", "sppr",
        "--control-ref", "ai-review-control", "--merge-request-id", "7",
    ]

    results = [runner.invoke(app, args) for _ in range(3)]

    assert [item.exit_code for item in results] == [0, 1, 3]
    assert client.get_authenticated_user.await_count == 3
    assert client.aclose.await_count == 3
    assert all(len(item.output.strip().splitlines()) == 1 for item in results)
