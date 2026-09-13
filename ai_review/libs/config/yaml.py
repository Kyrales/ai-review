from pathlib import Path
import os
from typing import Any

import yaml
from pydantic_settings import YamlConfigSettingsSource


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        seen: set[str] = set()
        for key_node, _ in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=False)
            normalized = str(key).casefold()
            if normalized in seen:
                raise ValueError(f"duplicate YAML key: {key}")
            seen.add(normalized)
        return super().construct_mapping(node, deep=deep)


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeySafeLoader)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("YAML configuration root must be a mapping")
    return data


class StrictYamlConfigSettingsSource(YamlConfigSettingsSource):
    def __call__(self) -> dict[str, Any]:
        path = self.config.get("yaml_file") or os.getenv("AI_REVIEW_CONFIG_FILE_YAML") or ".ai-review.yaml"
        file = Path(path)
        return load_yaml(file) if file.exists() else {}
