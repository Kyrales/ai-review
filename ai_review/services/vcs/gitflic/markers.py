import base64
import re
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator


PREFIX = "🤖 **AI-ревьювер**"
_MARKER = re.compile(r"<!-- ai-review:([^>]+) -->")
_MARKER_LIKE = re.compile(r"<!--\s*ai-review:", re.IGNORECASE)
_ALLOWED_KEYS = {
    "kind", "head", "status", "covered", "verdict", "location",
    "new_path", "old_path", "new_line", "old_line",
}


class MarkerKind(StrEnum):
    FINDING = "finding"
    SUMMARY = "summary"
    FOLLOWUP = "followup"


class ReviewMarker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["v1"] = "v1"
    kind: MarkerKind
    head: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    status: Literal["complete", "complete_with_warnings", "complete_with_partial_recovery"] | None = None
    covered: tuple[UUID, ...] = ()
    verdict: Literal["fixed", "open", "clarify"] | None = None
    location: Literal["fallback"] | None = None
    new_path: str | None = None
    old_path: str | None = None
    new_line: int | None = None
    old_line: int | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "ReviewMarker":
        if len(self.covered) > 50 or len(set(self.covered)) != len(self.covered):
            raise ValueError("covered UUIDs must be unique and contain at most 50 values")
        if self.kind is MarkerKind.FOLLOWUP:
            if self.verdict is None or self.status is not None:
                raise ValueError("followup marker requires verdict and no status")
        elif self.kind is MarkerKind.SUMMARY:
            if self.status is None or self.verdict is not None:
                raise ValueError("summary marker requires status and no verdict")
        elif self.status is not None or self.verdict is not None or self.covered:
            raise ValueError("finding marker cannot contain status, verdict or covered UUIDs")

        location_values = (self.new_path, self.old_path, self.new_line, self.old_line)
        if self.location == "fallback":
            if self.kind is not MarkerKind.FINDING:
                raise ValueError("fallback location is only valid for finding markers")
            if not self.new_path or not self.old_path or self.new_line is not None:
                raise ValueError("fallback marker requires paths and no new line")
            if self.old_line is None or self.old_line < 1:
                raise ValueError("fallback marker requires a positive old line")
        elif any(value is not None for value in location_values):
            raise ValueError("location fields require fallback location")
        return self


def _encode_path(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


def _decode_path(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("path must be base64url")
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
    if _encode_path(decoded) != value:
        raise ValueError("path must use canonical base64url")
    return decoded


def render_marker(marker: ReviewMarker) -> str:
    fields = ["v1", f"kind={marker.kind}"]
    if marker.status:
        fields.append(f"status={marker.status}")
    fields.append(f"head={marker.head}")
    if marker.location:
        fields.extend([
            "location=fallback",
            f"new_path={_encode_path(marker.new_path or '')}",
            f"old_path={_encode_path(marker.old_path or '')}",
            f"new_line={'null' if marker.new_line is None else marker.new_line}",
            f"old_line={'null' if marker.old_line is None else marker.old_line}",
        ])
    if marker.covered:
        fields.append("covered=" + ",".join(str(item) for item in marker.covered))
    if marker.verdict:
        fields.append(f"verdict={marker.verdict}")
    return "<!-- ai-review:" + ";".join(fields) + " -->"


def parse_marker(body: str, author_id: str | int | None, trusted_author_id: str | int) -> ReviewMarker | None:
    if str(author_id) != str(trusted_author_id):
        return None
    matches = _MARKER.findall(body)
    if len(matches) != 1 or len(_MARKER_LIKE.findall(body)) != 1:
        return None
    parts = matches[0].split(";")
    if not parts or parts[0] != "v1":
        return None
    raw: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            return None
        key, value = part.split("=", 1)
        if key not in _ALLOWED_KEYS or key in raw or not value:
            return None
        raw[key] = value
    try:
        covered_raw = raw.get("covered")
        if covered_raw is None:
            covered = ()
        else:
            covered_parts = covered_raw.split(",")
            if any(not item for item in covered_parts):
                return None
            covered = tuple(UUID(item) for item in covered_parts)
        values: dict[str, object] = {
            "kind": raw["kind"], "head": raw["head"], "covered": covered,
            "status": raw.get("status"), "verdict": raw.get("verdict"),
            "location": raw.get("location"),
        }
        location_keys = {"new_path", "old_path", "new_line", "old_line"}
        if values["location"]:
            if location_keys - raw.keys():
                return None
            values.update({
                "new_path": _decode_path(raw["new_path"]),
                "old_path": _decode_path(raw["old_path"]),
                "new_line": None if raw["new_line"] == "null" else int(raw["new_line"]),
                "old_line": None if raw["old_line"] == "null" else int(raw["old_line"]),
            })
        elif location_keys & raw.keys():
            return None
        return ReviewMarker.model_validate(values)
    except (KeyError, TypeError, ValueError):
        return None


def decorate_ai_message(text: str, marker: ReviewMarker) -> str:
    safe_text = _MARKER_LIKE.sub("&lt;!-- ai-review:", text)
    return f"{PREFIX}\n\n{safe_text}\n\n{render_marker(marker)}"
