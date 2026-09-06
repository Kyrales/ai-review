from pathlib import Path

from typer.testing import CliRunner

from ai_review.cli.main import app
from ai_review.libs.constants.vcs_provider import VCSProvider


def test_gitflic_image_contract() -> None:
    assert VCSProvider.GITFLIC.value == "GITFLIC"

    result = CliRunner().invoke(app, ["run-followup", "--help"])

    assert result.exit_code == 0
    assert "trusted GitFlic findings" in result.output


def test_image_uses_cli_entrypoint_in_mounted_source_directory() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")

    assert 'WORKDIR /review' in dockerfile
    assert 'ENTRYPOINT ["ai-review"]' in dockerfile
