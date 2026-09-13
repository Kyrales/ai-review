import json
from typing import Any


class StrictJSONError(ValueError):
    """The input is not a single, bounded, duplicate-free JSON value."""


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    seen: set[str] = set()
    for key, value in pairs:
        canonical = key.casefold()
        if canonical in seen:
            raise StrictJSONError(f"duplicate JSON key: {key}")
        seen.add(canonical)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise StrictJSONError(f"non-finite JSON number: {value}")


def _depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_depth(item) for item in value), default=0)
    return 0


def loads_strict(raw: str, *, max_depth: int = 16) -> Any:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except StrictJSONError:
        raise
    except (json.JSONDecodeError, TypeError, RecursionError) as error:
        raise StrictJSONError("invalid JSON") from error
    if _depth(value) > max_depth:
        raise StrictJSONError(f"JSON nesting exceeds {max_depth}")
    return value
