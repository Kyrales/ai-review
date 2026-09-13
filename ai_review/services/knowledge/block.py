import json
import re
from collections import Counter
from collections.abc import Sequence

from pydantic import TypeAdapter, ValidationError

from ai_review.services.knowledge.json import StrictJSONError, loads_strict
from ai_review.services.knowledge.schema import KnowledgeCandidate

TAG = "#ai-review-knowledge"
_TAG_RE = re.compile(r"(?m)^#ai-review-knowledge$")
_BLOCK_RE = re.compile(
    r"(?m)^#ai-review-knowledge$\n\n```json\n(?P<payload>.*?)\n```(?=\n|$)",
    re.DOTALL,
)
_CANDIDATES = TypeAdapter(tuple[KnowledgeCandidate, ...])


def render_knowledge_block(items: Sequence[KnowledgeCandidate]) -> str:
    if not items or len(items) > 150:
        raise ValueError("knowledge block must contain between 1 and 150 rules")
    counts = Counter(item.source_reply_id for item in items)
    if any(count > 3 for count in counts.values()):
        raise ValueError("knowledge block contains more than three rules for one reply")
    payload = {
        "rules": [item.model_dump(by_alias=True) for item in items],
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    return f"{TAG}\n\n```json\n{encoded}\n```"


def parse_knowledge_block(body: str) -> tuple[KnowledgeCandidate, ...] | None:
    if len(_TAG_RE.findall(body)) != 1:
        return None
    match = _BLOCK_RE.search(body)
    if match is None:
        return None
    try:
        root = loads_strict(match.group("payload"))
        if not isinstance(root, dict) or set(root) != {"rules"}:
            return None
        rules = root["rules"]
        if not isinstance(rules, list) or not 1 <= len(rules) <= 150:
            return None
        items = _CANDIDATES.validate_python(rules)
    except (StrictJSONError, ValidationError):
        return None
    counts = Counter(item.source_reply_id for item in items)
    if any(count > 3 for count in counts.values()):
        return None
    return items
