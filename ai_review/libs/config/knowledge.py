from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Immutable(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class KnowledgeSyncConfig(_Immutable):
    rules_file: str = "ai-review/prompts/project-rules.md"


class TrustedReviewersConfig(_Immutable):
    gitflic: tuple[str, ...] = ()
    gitlab: tuple[str, ...] = ()

    @field_validator("gitflic", "gitlab", mode="before")
    @classmethod
    def unique_usernames(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list):
            raise ValueError("trusted reviewer usernames must be a YAML list")
        result = []
        seen = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError("trusted reviewer usernames must be strings")
            name = item.strip()
            if not name:
                raise ValueError("trusted reviewer usernames must not be empty")
            key = name.casefold()
            if key not in seen:
                seen.add(key)
                result.append(name)
        return tuple(result)


class KnowledgeConfig(_Immutable):
    enabled: bool = False
    trusted_reviewers: TrustedReviewersConfig = Field(default_factory=TrustedReviewersConfig)
    max_rules_per_reply: int = Field(default=3, ge=1, le=3)
    sync: KnowledgeSyncConfig = Field(default_factory=KnowledgeSyncConfig)


def resolve_rules_path_from_config(config_path: Path, repository_root: Path, rules_file: str) -> Path:
    if (not rules_file.endswith(".md") or Path(rules_file).is_absolute()
            or any(part == ".." for part in Path(rules_file).parts)
            or re.search(r"[*?\[\]{}]", rules_file) or ":" in rules_file):
        raise ValueError("rules_file must be a safe relative path")
    candidate = (config_path.parent / rules_file).resolve()
    root = repository_root.resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("knowledge.sync.rules_file escapes repository root")
    return candidate
