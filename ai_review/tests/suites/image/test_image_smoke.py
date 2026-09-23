from pathlib import Path

from typer.testing import CliRunner

from ai_review.cli.main import app
from ai_review.libs.constants.vcs_provider import VCSProvider


def test_gitflic_image_contract() -> None:
    assert VCSProvider.GITFLIC.value == "GITFLIC"

    result = CliRunner().invoke(app, ["run-followup", "--help"])

    assert result.exit_code == 0
    assert "trusted GitFlic findings" in result.output

    dispatch = CliRunner().invoke(app, ["gitflic-dispatch", "--help"])
    assert dispatch.exit_code == 0
    assert all(name in dispatch.output for name in ("--all", "--merge-request-id", "--control-ref"))
    assert "token" not in dispatch.output.casefold()


def test_image_uses_cli_entrypoint_in_mounted_source_directory() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert 'WORKDIR /review' in dockerfile
    assert 'ENTRYPOINT ["ai-review"]' in dockerfile


def test_dispatch_publish_and_documentation_contract() -> None:
    workflow = Path(".github/workflows/workflow-publish.yml").read_text(encoding="utf-8")
    documentation = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in ("README.md", "docs/cli/README.md", "docs/ci/README.md")
    )

    assert "gitflic-dispatch --help" in workflow
    assert "--merge-request-id" in documentation
    assert "--all" in documentation
    assert "AI_REVIEW_GITFLIC_TOKEN" in documentation
    assert "host Python" in documentation
    assert all(value in documentation for value in ("exit 0", "exit 1", "exit 2", "exit 3"))
