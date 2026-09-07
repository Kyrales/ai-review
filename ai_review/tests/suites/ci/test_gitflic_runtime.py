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


def test_publish_smoke_uses_embedded_config_without_artifact_overrides():
    workflow = (ROOT / ".github/workflows/workflow-publish.yml").read_text(encoding="utf-8")

    assert "AI_REVIEW_CONFIG_FILE_YAML=/opt/ai-review-ci/sppr/.ai-review-ones.yaml" in workflow
    assert "ARTIFACTS__LLM_DIR" not in workflow


def test_prepare_refuses_untrusted_urls_before_tokens_are_used():
    script = (ROOT / "ci/gitflic/prepare.sh").read_text(encoding="utf-8")

    assert "validate_https_url" in script
    assert 'https://api.gitflic.ru/project/rt-vt/sppr/merge-request/' in script
    assert 'https://gitflic.ru/project/rt-vt/sppr.git' in script
    assert 'https://codex.kpkrs.ru/backend-api/codex' in script
    assert "actual.username" in script
    assert "query" in script
    assert "redirect refused" in script


def test_prepare_credentials_and_runtime_cleanup_have_a_safety_contract():
    script = (ROOT / "ci/gitflic/prepare.sh").read_text(encoding="utf-8")

    assert "credential_canary" in script
    assert "config --get-regexp" in script
    assert "ps -eo args" in script
    assert "find \"$artifacts\"" in script
    assert "trap cleanup EXIT HUP INT TERM" in script
    assert "maintenance.lock" in script
    assert "AI_REVIEW_WORK_TTL_SECONDS" in script
    assert "find \"$runtime/work\"" in script


def test_ci_contract_keeps_image_config_prompts_and_secrets_out_of_regular_jobs():
    config = (ROOT / "ci/sppr/.ai-review-ones.yaml").read_text(encoding="utf-8")

    assert "COPY ci /opt/ai-review-ci" in (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "/opt/ai-review-ci/sppr/prompts/" in config
    assert "${" not in config
