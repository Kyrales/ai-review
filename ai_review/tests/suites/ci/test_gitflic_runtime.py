from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]


def test_image_contains_self_contained_gitflic_prepare_runtime():
    script = (ROOT / "ci/gitflic/prepare.sh").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "gitflic.ru/project/rt-vt/sppr.git" in script
    assert "--depth" not in script
    assert "merge-base" in script
    assert "+refs/heads/" in script
    assert "GIT_ASKPASS" in script
    assert "mktemp /tmp" not in script
    assert 'mktemp "$runtime/work/' in script
    assert "COPY ci /opt/ai-review-ci" in dockerfile


def test_sppr_runtime_config_uses_embedded_russian_prompts():
    config = (ROOT / "ci/sppr/.ai-review-ones.yaml").read_text(encoding="utf-8")
    prompt = (ROOT / "ci/sppr/prompts/inline.md").read_text(encoding="utf-8")

    assert "/opt/ai-review-ci/sppr/prompts/inline.md" in config
    assert "Проведи ревью" in prompt


def test_publish_workflow_keylessly_signs_published_digest():
    workflow = (ROOT / ".github/workflows/workflow-publish.yml").read_text(encoding="utf-8")

    assert "id-token: write" in workflow
    assert "sigstore/cosign-installer@" in workflow
    assert "cosign sign --yes" in workflow
    assert "ghcr.io/kyrales/ai-review@sha256:*" in workflow
