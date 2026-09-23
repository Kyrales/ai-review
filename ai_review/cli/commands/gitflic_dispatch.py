import os
from collections.abc import Sequence
from enum import IntEnum
from urllib.parse import urlsplit
from uuid import UUID

import typer

from ai_review.clients.gitflic.client import GitFlicHTTPClient
from ai_review.libs.config.http import HTTPClientWithTokenConfig
from ai_review.services.dispatch.models import DispatchReport, DispatchSelection
from ai_review.services.dispatch.service import GitFlicDispatchService, is_safe_ref


class DispatchExitCode(IntEnum):
    OK = 0
    PARTIAL = 1
    USAGE = 2
    SOURCE = 3


def _config_error() -> DispatchReport:
    return DispatchReport(
        status="fatal", failure_scope="source", error_code="invalid_configuration",
        selected_count=0, started_count=0, skipped_count=0, failed_count=0, items=(),
    )


def _validate_text(value: str) -> bool:
    return bool(value) and not any(ord(char) < 32 or ord(char) == 127 for char in value)


def _validate_alias(value: str) -> bool:
    return _validate_text(value) and all(
        component not in {"", ".", ".."} for component in value.split("/")
    )


def _validate_api_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def _canonical_uuid(value: object) -> str:
    parsed = UUID(str(value))
    if parsed.version not in range(1, 6):
        raise ValueError("trusted author must be a UUID")
    return str(parsed)


async def run_gitflic_dispatch_command(
    *,
    owner: str,
    project: str,
    control_ref: str,
    api_url: str,
    merge_request_ids: Sequence[int],
    all_open: bool,
) -> int:
    token = os.environ.get("AI_REVIEW_GITFLIC_TOKEN", "")
    fallback = os.environ.get("AI_REVIEW_GITFLIC_TOKEN2") or None
    if (
        not token
        or fallback == token
        or not _validate_alias(owner)
        or not _validate_alias(project)
        or not _validate_text(control_ref)
        or not is_safe_ref(control_ref)
        or not _validate_api_url(api_url)
    ):
        typer.echo(_config_error().model_dump_json())
        return DispatchExitCode.USAGE
    try:
        selection = DispatchSelection(
            all_open=all_open, merge_request_ids=tuple(merge_request_ids)
        )
        config = HTTPClientWithTokenConfig(api_url=api_url, api_token=token)
    except Exception:
        typer.echo(_config_error().model_dump_json())
        return DispatchExitCode.USAGE

    client = GitFlicHTTPClient(config=config, fallback_token=fallback)
    try:
        author_id = os.environ.get("AI_REVIEW_GITFLIC_USER_ID")
        if not author_id:
            author_id = (await client.get_authenticated_user()).id
        trusted_author_id = _canonical_uuid(author_id)
        report = await GitFlicDispatchService(
            client, owner, project, control_ref, trusted_author_id
        ).run(selection)
    except (ValueError, TypeError):
        report = _config_error()
        code = DispatchExitCode.USAGE
    except Exception:
        report = DispatchReport(
            status="fatal", failure_scope="source", error_code="source_preflight_failed",
            selected_count=0, started_count=0, skipped_count=0, failed_count=0, items=(),
        )
        code = DispatchExitCode.SOURCE
    else:
        code = {
            "success": DispatchExitCode.OK,
            "partial": DispatchExitCode.PARTIAL,
            "fatal": DispatchExitCode.SOURCE,
        }[report.status]
    finally:
        await client.aclose()
    typer.echo(report.model_dump_json())
    return code
