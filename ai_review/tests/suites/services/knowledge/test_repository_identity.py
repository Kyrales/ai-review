from pathlib import Path

import pytest

from ai_review.libs.constants.vcs_provider import VCSProvider
from ai_review.services.knowledge.repository_identity import (
    IdentityResolutionError,
    resolve_repository_identity,
)


@pytest.mark.parametrize(
    "remote",
    [
        "https://gitflic.ru/project/team/demo.git",
        "git@gitflic.ru:team/demo.git",
        "ssh://git@gitflic.ru/team/demo.git",
    ],
)
def test_resolves_gitflic_identity_from_supported_origin_urls(tmp_path: Path, remote: str):
    """Catches failure to move the launcher unchanged between GitFlic repositories."""
    value = resolve_repository_identity(
        tmp_path, origin_url=remote,
        environ={"AI_REVIEW_GITFLIC_TOKEN": "secret", "AI_REVIEW_GITFLIC_USER_ID": "ai-id"},
    )
    assert (value.provider, value.host, value.owner, value.project, value.api_url) == (
        VCSProvider.GITFLIC, "gitflic.ru", "team", "demo", "https://api.gitflic.ru",
    )
    assert value.token.get_secret_value() == "secret"
    assert value.trusted_ai_id == "ai-id"
    assert "secret" not in value.diagnostic


def test_cli_overrides_environment_then_origin(tmp_path: Path):
    """Catches repository identity precedence being applied backwards."""
    value = resolve_repository_identity(
        tmp_path,
        overrides={"owner": "cli", "project": "chosen", "provider": "GITFLIC"},
        environ={
            "VCS__PIPELINE__OWNER": "env", "VCS__PIPELINE__PROJECT": "env-project",
            "VCS__HTTP_CLIENT__API_TOKEN": "token", "AI_REVIEW_GITFLIC_USER_ID": "ai",
        },
        origin_url="https://gitflic.ru/project/remote/remote.git",
    )
    assert (value.owner, value.project) == ("cli", "chosen")


def test_self_hosted_requires_explicit_api_url(tmp_path: Path):
    """Catches accidental requests to a guessed API on an unknown host."""
    with pytest.raises(IdentityResolutionError, match="API URL"):
        resolve_repository_identity(
            tmp_path, origin_url="https://git.example/project/team/demo.git",
            overrides={"provider": "GITFLIC"},
            environ={"GITFLIC_TOKEN": "secret", "AI_REVIEW_GITFLIC_USER_ID": "ai"},
        )


def test_dotenv_token_and_authenticated_identity_fallback(tmp_path: Path):
    """Catches ignoring established local credential names and provider identity fallback."""
    value = resolve_repository_identity(
        tmp_path, origin_url="git@gitflic.ru:team/demo.git", environ={},
        dotenv={"GITFLIC_TOKEN": "dotenv-secret"}, authenticated_user_id="from-api",
    )
    assert value.token.get_secret_value() == "dotenv-secret"
    assert value.trusted_ai_id == "from-api"


def test_complete_cli_identity_does_not_require_origin_remote(tmp_path: Path):
    """Catches explicit portable launcher overrides still depending on local git metadata."""
    value = resolve_repository_identity(
        tmp_path,
        overrides={
            "provider": "GITFLIC", "host": "gitflic.ru", "owner": "team",
            "project": "demo", "api_url": "https://api.gitflic.ru",
        },
        environ={"GITFLIC_TOKEN": "token", "AI_REVIEW_GITFLIC_USER_ID": "ai"},
    )
    assert (value.owner, value.project) == ("team", "demo")
