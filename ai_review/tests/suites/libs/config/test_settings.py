from ai_review.config import Settings
from ai_review.libs.config.artifacts import ArtifactsConfig


def test_artifacts_default_is_created_lazily():
    field = Settings.model_fields["artifacts"]

    assert field.default_factory is ArtifactsConfig
