import asyncio
from pathlib import Path
import typer

app = typer.Typer(help="AI Review CLI")

@app.command("run")
def run():
    """Run the full AI review pipeline"""
    from ai_review.cli.commands.run_review import run_review_command
    typer.secho("Starting full AI review...", fg=typer.colors.CYAN, bold=True)
    asyncio.run(run_review_command())
    typer.secho("AI review completed successfully!", fg=typer.colors.GREEN, bold=True)

@app.command("run-inline")
def run_inline():
    """Run only the inline review"""
    from ai_review.cli.commands.run_inline_review import run_inline_review_command
    typer.secho("Starting inline AI review...", fg=typer.colors.CYAN)
    asyncio.run(run_inline_review_command())
    typer.secho("AI review completed successfully!", fg=typer.colors.GREEN, bold=True)

@app.command("run-context")
def run_context():
    """Run only the context review"""
    from ai_review.cli.commands.run_context_review import run_context_review_command
    typer.secho("Starting context AI review...", fg=typer.colors.CYAN)
    asyncio.run(run_context_review_command())
    typer.secho("AI review completed successfully!", fg=typer.colors.GREEN, bold=True)

@app.command("run-summary")
def run_summary():
    """Run only the summary review"""
    from ai_review.cli.commands.run_summary_review import run_summary_review_command
    typer.secho("Starting summary AI review...", fg=typer.colors.CYAN)
    asyncio.run(run_summary_review_command())
    typer.secho("AI review completed successfully!", fg=typer.colors.GREEN, bold=True)

@app.command("run-inline-reply")
def run_inline_reply():
    """Run only the inline reply review"""
    from ai_review.cli.commands.run_inline_reply_review import run_inline_reply_review_command
    typer.secho("Starting inline reply AI review...", fg=typer.colors.CYAN)
    asyncio.run(run_inline_reply_review_command())
    typer.secho("AI review completed successfully!", fg=typer.colors.GREEN, bold=True)

@app.command("run-summary-reply")
def run_summary_reply():
    """Run only the summary reply review"""
    from ai_review.cli.commands.run_summary_reply_review import run_summary_reply_review_command
    typer.secho("Starting summary reply AI review...", fg=typer.colors.CYAN)
    asyncio.run(run_summary_reply_review_command())
    typer.secho("AI review completed successfully!", fg=typer.colors.GREEN, bold=True)

@app.command("run-followup")
def run_followup():
    """Re-check developer replies to trusted GitFlic findings."""
    from ai_review.cli.commands.run_followup_review import run_followup_review_command
    asyncio.run(run_followup_review_command())

@app.command("clear-inline")
def clear_inline():
    """Remove all AI-generated inline review comments"""
    from ai_review.cli.commands.run_clear_inline_review import run_clear_inline_review
    typer.secho("Clearing inline AI review comments...", fg=typer.colors.YELLOW)
    asyncio.run(run_clear_inline_review())
    typer.secho("Inline AI comments cleared", fg=typer.colors.GREEN, bold=True)

@app.command("clear-summary")
def clear_summary():
    """Remove all AI-generated summary review comments"""
    from ai_review.cli.commands.run_clear_summary_review import run_clear_summary_review
    typer.secho("Clearing summary AI review comments...", fg=typer.colors.YELLOW)
    asyncio.run(run_clear_summary_review())
    typer.secho("Summary AI comments cleared", fg=typer.colors.GREEN, bold=True)

@app.command("show-config")
def show_config():
    """Show the current resolved configuration"""
    from ai_review.config import load_settings
    typer.secho("Loaded AI Review configuration:", fg=typer.colors.CYAN, bold=True)
    typer.echo(load_settings().model_dump_json(indent=2, exclude_none=True))

@app.command("sync-knowledge")
def sync_knowledge(
    repository_root: Path = typer.Option(Path.cwd(), "--repository-root"),
    config: Path | None = typer.Option(None, "--config"),
    merge_request_id: list[str] | None = typer.Option(None, "--merge-request-id"),
    all_: bool = typer.Option(False, "--all"),
    yes: bool = typer.Option(False, "--yes"),
    provider: str | None = typer.Option(None, "--provider"),
    host: str | None = typer.Option(None, "--host"),
    owner: str | None = typer.Option(None, "--owner"),
    project: str | None = typer.Option(None, "--project"),
    api_url: str | None = typer.Option(None, "--api-url"),
):
    """Synchronize project knowledge rules."""
    from ai_review.cli.commands.sync_knowledge import run_sync_knowledge_command
    code = asyncio.run(run_sync_knowledge_command(
        repository_root=repository_root, config=config,
        merge_request_ids=merge_request_id or (), all_=all_, yes=yes,
        provider=provider, host=host, owner=owner, project=project, api_url=api_url,
    ))
    if code:
        raise typer.Exit(code=int(code))

if __name__ == "__main__":
    app()
