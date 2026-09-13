from pathlib import Path
from unittest.mock import patch
import pytest
from ai_review.libs.config.knowledge import KnowledgeConfig, TrustedReviewersConfig, resolve_rules_path_from_config
from ai_review.libs.config.settings import load_sync_settings

def test_defaults_and_disabled():
    cfg = KnowledgeConfig(); assert cfg.enabled is False and cfg.max_rules_per_reply == 3

def test_reviewer_names_are_case_insensitive_unique():
    assert TrustedReviewersConfig(gitflic=[" Alice ", "alice", "Bob"]).gitflic == ("Alice", "Bob")

@pytest.mark.parametrize("value", ["", "  ", 1])
def test_reviewer_names_are_nonempty_strings(value):
    with pytest.raises(Exception): TrustedReviewersConfig(gitlab=[value])

def test_reviewer_allowlist_must_be_a_list():
    with pytest.raises(Exception): TrustedReviewersConfig(gitlab="alice")

@pytest.mark.parametrize("value", [0, 4])
def test_max_rules_bounds(value):
    with pytest.raises(Exception): KnowledgeConfig(max_rules_per_reply=value)

@pytest.mark.parametrize("rules_file", ["/tmp/x.md", "../x.md", "x/*.md", "https://x.md", "x.txt"])
def test_rules_path_rejects_unsafe_values(tmp_path: Path, rules_file: str):
    with pytest.raises(ValueError): resolve_rules_path_from_config(tmp_path / "config.yaml", tmp_path, rules_file)

def test_rules_path_rejects_symlink_escape(tmp_path: Path):
    original_resolve = Path.resolve
    outside = tmp_path.parent / "outside.md"
    def resolve(path: Path, *args, **kwargs):
        return outside if path.name == "link.md" else original_resolve(path, *args, **kwargs)
    with patch.object(Path, "resolve", resolve):
        with pytest.raises(ValueError):
            resolve_rules_path_from_config(tmp_path / "config.yaml", tmp_path, "link.md")

@pytest.mark.parametrize("body", [
    "knowledge:\n  enabled: true\n  Enabled: false\n",
    "knowledge:\n  enabled: !foo true\n",
    "knowledge:\n  unexpected: true\n",
    "knowledge:\n  enabled: true\nKnowledge:\n  enabled: false\n",
    "knowledge:\n  enabled: true\nknowledge:\n  enabled: false\n",
])
def test_knowledge_yaml_fails_closed(tmp_path: Path, body: str):
    path = tmp_path / "config.yaml"; path.write_text(body)
    with pytest.raises(Exception): load_sync_settings(path)


@pytest.mark.parametrize("body", [
    "knowledge: &k\n  enabled: true\n",
    "knowledge:\n  enabled: !!bool true\n",
    "!!str knowledge:\n  enabled: true\n",
    "knowledge: {enabled: true}\n",
    "defaults: &defaults\n  enabled: true\nknowledge:\n  <<: *defaults\n",
])
def test_knowledge_yaml_accepts_safe_standard_yaml(tmp_path: Path, body: str):
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")

    assert load_sync_settings(path).knowledge.enabled is True
