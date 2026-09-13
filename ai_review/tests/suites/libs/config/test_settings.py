from ai_review.config import Settings
from ai_review.libs.config.artifacts import ArtifactsConfig
from ai_review.libs.config.review import ReviewConfig, ReviewSeverity
from ai_review.libs.config.settings import load_sync_settings


def test_artifacts_default_is_created_lazily():
    field = Settings.model_fields["artifacts"]

    assert field.default_factory is ArtifactsConfig


def test_review_publishes_medium_and_higher_severities_by_default():
    assert ReviewConfig().publish_severities == [
        ReviewSeverity.CRITICAL,
        ReviewSeverity.HIGH,
        ReviewSeverity.MEDIUM,
    ]

def test_knowledge_field_has_defaults():
    assert Settings.model_fields["knowledge"].default_factory is not None


def test_explicit_loader_does_not_mutate_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_REVIEW_CONFIG_FILE_YAML", "environment.yaml")
    config = tmp_path / "config.yaml"
    config.write_text("knowledge:\n  enabled: true\n", encoding="utf-8")
    load_sync_settings(config)
    assert __import__("os").environ["AI_REVIEW_CONFIG_FILE_YAML"] == "environment.yaml"


def test_yaml_files_are_always_read_as_utf8():
    assert Settings.model_config["yaml_file_encoding"] == "utf-8"
