from pathlib import Path


def test_config_template_documents_gitflic_provider() -> None:
    template = Path("docs/configs/.ai-review.yaml").read_text(encoding="utf-8")

    assert "GITFLIC" in template
    assert "merge_request_id: ${AI_REVIEW_GITFLIC_MERGE_REQUEST_ID}" in template
    assert "api_url: ${AI_REVIEW_GITFLIC_API_URL}" in template
