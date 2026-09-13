from pathlib import Path

import yaml


def test_config_template_documents_gitflic_provider() -> None:
    template = Path("docs/configs/.ai-review.yaml").read_text(encoding="utf-8")

    assert "GITFLIC" in template
    assert "merge_request_id: ${AI_REVIEW_GITFLIC_MERGE_REQUEST_ID}" in template
    assert "api_url: ${AI_REVIEW_GITFLIC_API_URL}" in template


def test_config_template_keeps_knowledge_opt_in_and_documents_all_fields() -> None:
    """Catches an unsafe default or an incomplete portable configuration example."""
    template = yaml.safe_load(
        Path("docs/configs/.ai-review.yaml").read_text(encoding="utf-8")
    )

    assert template["knowledge"] == {
        "enabled": False,
        "trusted_reviewers": {"gitflic": [], "gitlab": []},
        "max_rules_per_reply": 3,
        "sync": {"rules_file": "ai-review/prompts/project-rules.md"},
    }
