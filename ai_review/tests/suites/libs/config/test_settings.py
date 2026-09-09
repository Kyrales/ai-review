from ai_review.config import Settings
from ai_review.libs.config.artifacts import ArtifactsConfig
from ai_review.libs.config.review import ReviewConfig, ReviewSeverity


def test_artifacts_default_is_created_lazily():
    field = Settings.model_fields["artifacts"]

    assert field.default_factory is ArtifactsConfig


def test_review_publishes_medium_and_higher_severities_by_default():
    assert ReviewConfig().publish_severities == [
        ReviewSeverity.CRITICAL,
        ReviewSeverity.HIGH,
        ReviewSeverity.MEDIUM,
    ]
