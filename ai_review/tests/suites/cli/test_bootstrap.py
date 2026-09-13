from pathlib import Path
import os
import subprocess
import sys
from ai_review.libs.config.settings import load_sync_settings

def test_sync_loader_does_not_require_legacy_sections(tmp_path: Path):
    config = tmp_path / "config.yaml"; config.write_text("knowledge:\n  enabled: true\n", encoding="utf-8")
    assert load_sync_settings(config).knowledge.enabled is True

def test_sync_cli_bootstraps_without_legacy_settings(tmp_path: Path):
    config = tmp_path / "config.yaml"; config.write_text("knowledge:\n  enabled: true\n")
    env = os.environ.copy(); env.pop("AI_REVIEW_CONFIG_FILE_YAML", None)
    help_result = subprocess.run([sys.executable, "-m", "ai_review.cli.main", "sync-knowledge", "--help"], env=env, capture_output=True, text=True)
    assert help_result.returncode == 0, help_result.stderr
    run_result = subprocess.run([sys.executable, "-m", "ai_review.cli.main", "sync-knowledge", "--config", str(config), "--all"], env=env, capture_output=True, text=True)
    assert run_result.returncode == 2
    assert "ValidationError" not in run_result.stderr
