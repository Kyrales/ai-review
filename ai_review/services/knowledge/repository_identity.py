from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Mapping

from dotenv import dotenv_values
from pydantic import SecretStr

from ai_review.libs.constants.vcs_provider import VCSProvider


class IdentityResolutionError(ValueError):
    pass


@dataclass(frozen=True)
class RepositoryIdentity:
    provider: VCSProvider
    host: str
    owner: str
    project: str
    api_url: str
    token: SecretStr
    trusted_ai_id: str

    @property
    def diagnostic(self) -> str:
        return f"{self.provider.value.lower()} {self.host} {self.owner}/{self.project} ({self.api_url})"


def _first(*values: object) -> str | None:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _origin(repository_root: Path) -> str:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"], cwd=repository_root,
        capture_output=True, text=True, check=False,
    )
    if result.returncode:
        raise IdentityResolutionError("cannot resolve origin remote")
    return result.stdout.strip()


def _parse_remote(remote: str) -> tuple[str, str, str]:
    patterns = (
        r"^https?://(?P<host>[^/]+)/project/(?P<owner>[^/]+)/(?P<project>[^/]+?)(?:\.git)?$",
        r"^git@(?P<host>[^:]+):(?P<owner>[^/]+)/(?P<project>[^/]+?)(?:\.git)?$",
        r"^ssh://git@(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<project>[^/]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, remote)
        if match:
            return match.group("host").lower(), match.group("owner"), match.group("project")
    raise IdentityResolutionError("unsupported or ambiguous origin remote")


def resolve_repository_identity(
    repository_root: Path,
    *,
    overrides: Mapping[str, object] | None = None,
    environ: Mapping[str, str] | None = None,
    dotenv: Mapping[str, object] | None = None,
    origin_url: str | None = None,
    authenticated_user_id: str | int | None = None,
    require_trusted_ai: bool = True,
) -> RepositoryIdentity:
    overrides = overrides or {}
    environ = os.environ if environ is None else environ
    dotenv = dotenv_values(repository_root / ".env") if dotenv is None else dotenv
    explicit_host = _first(overrides.get("host"))
    explicit_owner = _first(overrides.get("owner"), environ.get("VCS__PIPELINE__OWNER"))
    explicit_project = _first(overrides.get("project"), environ.get("VCS__PIPELINE__PROJECT"))
    if explicit_host and explicit_owner and explicit_project and origin_url is None:
        host, remote_owner, remote_project = explicit_host.lower(), explicit_owner, explicit_project
    else:
        host, remote_owner, remote_project = _parse_remote(origin_url or _origin(repository_root))
    provider_text = _first(overrides.get("provider"), environ.get("VCS__PROVIDER"), "GITFLIC")
    try:
        provider = VCSProvider(provider_text.upper())
    except (AttributeError, ValueError) as error:
        raise IdentityResolutionError("unsupported VCS provider") from error
    if provider is not VCSProvider.GITFLIC:
        raise IdentityResolutionError("knowledge sync adapter is available only for GitFlic")

    owner = _first(overrides.get("owner"), environ.get("VCS__PIPELINE__OWNER"), remote_owner)
    project = _first(overrides.get("project"), environ.get("VCS__PIPELINE__PROJECT"), remote_project)
    resolved_host = _first(overrides.get("host"), host)
    api_url = _first(overrides.get("api_url"), environ.get("VCS__HTTP_CLIENT__API_URL"), dotenv.get("VCS__HTTP_CLIENT__API_URL"))
    if api_url is None and resolved_host == "gitflic.ru":
        api_url = "https://api.gitflic.ru"
    if api_url is None:
        raise IdentityResolutionError("explicit API URL is required for self-hosted GitFlic")
    token = _first(
        environ.get("VCS__HTTP_CLIENT__API_TOKEN"), environ.get("AI_REVIEW_GITFLIC_TOKEN"), environ.get("GITFLIC_TOKEN"),
        dotenv.get("VCS__HTTP_CLIENT__API_TOKEN"), dotenv.get("AI_REVIEW_GITFLIC_TOKEN"), dotenv.get("GITFLIC_TOKEN"),
    )
    if token is None:
        raise IdentityResolutionError("GitFlic API token is missing")
    trusted_ai_id = _first(
        overrides.get("trusted_ai_id"), environ.get("AI_REVIEW_GITFLIC_USER_ID"),
        dotenv.get("AI_REVIEW_GITFLIC_USER_ID"), authenticated_user_id,
    )
    if trusted_ai_id is None and require_trusted_ai:
        raise IdentityResolutionError("trusted AI identity is missing")
    return RepositoryIdentity(
        provider=provider, host=resolved_host or host, owner=owner or "", project=project or "",
        api_url=api_url, token=SecretStr(token), trusted_ai_id=trusted_ai_id or "",
    )
