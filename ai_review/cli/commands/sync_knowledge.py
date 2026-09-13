from __future__ import annotations

from enum import IntEnum
from pathlib import Path
from collections.abc import Sequence

import typer

from ai_review.clients.gitflic.client import GitFlicHTTPClient
from ai_review.libs.config.http import HTTPClientWithTokenConfig
from ai_review.libs.config.knowledge import resolve_rules_path_from_config
from ai_review.libs.config.settings import load_sync_settings
from ai_review.services.knowledge.compiler import KnowledgeCompiler
from ai_review.services.knowledge.gitflic_source import GitFlicKnowledgeSource
from ai_review.services.knowledge.llm_gateway import KnowledgeLLMGateway
from ai_review.services.knowledge.repository_identity import resolve_repository_identity
from ai_review.services.knowledge.source import KnowledgeSourceService
from ai_review.services.knowledge.sync import KnowledgeSyncService


class ExitCode(IntEnum):
    OK = 0
    USAGE = 2
    SOURCE = 3
    COMPILER = 4
    WRITE = 5


async def build_runtime(
    *, repository_root: Path, config: Path | None = None,
    provider: str | None = None, host: str | None = None,
    owner: str | None = None, project: str | None = None,
    api_url: str | None = None,
) -> KnowledgeSyncService | None:
    root = repository_root.resolve()
    config_path = (config or root / ".ai-review-ones.yaml").resolve()
    settings = load_sync_settings(config_path)
    if not settings.knowledge.enabled:
        return None

    overrides = {key: value for key, value in {
        "provider": provider, "host": host, "owner": owner,
        "project": project, "api_url": api_url,
    }.items() if value is not None}
    identity = resolve_repository_identity(root, overrides=overrides, require_trusted_ai=False)
    http = GitFlicHTTPClient(
        config=HTTPClientWithTokenConfig(
            api_url=identity.api_url, api_token=identity.token, timeout=120, verify=True,
        )
    )
    if not identity.trusted_ai_id:
        current = await http.get_authenticated_user()
        identity = resolve_repository_identity(
            root, overrides=overrides, authenticated_user_id=current.id
        )
    source = GitFlicKnowledgeSource(http, identity.owner, identity.project)
    verified = KnowledgeSourceService(
        source, identity.trusted_ai_id,
        getattr(settings.knowledge.trusted_reviewers, identity.provider.value.lower()),
        settings.knowledge.max_rules_per_reply,
    )
    llm = KnowledgeLLMGateway(repository_root=root)
    rules_path = resolve_rules_path_from_config(
        config_path, root, settings.knowledge.sync.rules_file
    )
    return KnowledgeSyncService(verified, KnowledgeCompiler(llm), rules_path)


def _parse_selection(value: str, eligible: Sequence[str]) -> list[str]:
    value = value.strip()
    if not value:
        return []
    if value.casefold() in {"all", "все"}:
        return list(eligible)
    selected = [item.strip() for item in value.split(",") if item.strip()]
    if not selected or len(set(selected)) != len(selected) or any(item not in eligible for item in selected):
        raise ValueError(value)
    return selected


def _format_decision(decision) -> str:
    inputs = ", ".join(str(index) for index in decision.source_input_indexes)
    related = ", ".join(str(index) for index in decision.related_indexes)
    target = "-" if decision.target_managed_index is None else decision.target_managed_index
    rule = "-" if decision.result_rule is None else decision.result_rule
    return (
        f"inputs=[{inputs}]; action={decision.action}; target={target}; "
        f"result_rule={rule}; related=[{related}]; reason={decision.reason}"
    )


async def run_sync_knowledge_command(
    *, repository_root: Path, config: Path | None, merge_request_ids: Sequence[str],
    all_: bool, yes: bool, provider: str | None, host: str | None,
    owner: str | None, project: str | None, api_url: str | None,
) -> int:
    try:
        service = await build_runtime(
            repository_root=repository_root, config=config, provider=provider, host=host,
            owner=owner, project=project, api_url=api_url,
        )
    except Exception as error:
        typer.echo(f"Ошибка настройки: {error}", err=True)
        return ExitCode.USAGE
    if service is None:
        typer.echo("Накопление знаний выключено в конфигурации.")
        return ExitCode.OK

    try:
        rows = await service.discover()
    except Exception as error:
        typer.echo(f"Ошибка чтения merge requests: {error}", err=True)
        return ExitCode.SOURCE
    typer.echo("MR    Ветка                 Блоков знаний    Предлагаемых правил")
    eligible = []
    for row in rows:
        if row.error:
            typer.echo(f"#{row.review.id:<4} {row.review.source_branch:<21} ОШИБКА: {row.error}")
        else:
            eligible.append(str(row.review.id))
            typer.echo(f"#{row.review.id:<4} {row.review.source_branch:<21} {row.block_count:<16} {row.rule_count}")
    if not eligible:
        typer.echo("Нет открытых MR с проверенными знаниями.")
        return ExitCode.OK

    try:
        if all_:
            selected = eligible
        elif merge_request_ids:
            selected = _parse_selection(",".join(merge_request_ids), eligible)
        else:
            selected = _parse_selection(typer.prompt("Выберите MR (номера, all/все; Enter — выход)", default="", show_default=False), eligible)
    except (ValueError, typer.Abort) as error:
        typer.echo(f"Некорректный или недоступный MR: {error}", err=True)
        return ExitCode.USAGE
    if not selected:
        typer.echo("Отменено.")
        return ExitCode.OK

    try:
        preview = await service.preview(selected)
    except Exception as error:
        typer.echo(f"Ошибка подготовки изменений: {error}", err=True)
        return ExitCode.COMPILER
    for decision in preview.decisions:
        typer.echo(_format_decision(decision))
    if preview.conflicts:
        typer.echo("Конфликты:")
        for conflict in preview.conflicts:
            typer.echo(f"- {_format_decision(conflict)}")
    typer.echo(preview.diff or "Изменений нет.")
    typer.echo("; ".join(f"{name}: {count}" for name, count in preview.counts.items()))
    if not preview.diff:
        return ExitCode.OK
    if not yes and not typer.confirm("Записать изменения?", default=False):
        typer.echo("Отменено.")
        return ExitCode.OK
    try:
        service.commit(preview)
    except Exception as error:
        typer.echo(f"Ошибка записи: {error}", err=True)
        return ExitCode.WRITE
    typer.echo("Правила обновлены локально.")
    return ExitCode.OK
