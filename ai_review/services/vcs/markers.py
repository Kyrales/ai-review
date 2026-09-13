import base64
import hashlib
import re
import struct
import unicodedata
from enum import StrEnum
from typing import Annotated, Iterable, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from ai_review.libs.constants.vcs_provider import VCSProvider


PREFIX = "🤖 **AI-ревьювер**"
_MARKER = re.compile(r"<!-- ai-review:([^>]+) -->")
_MARKER_LIKE = re.compile(r"<!--\s*ai-review:", re.IGNORECASE)
_V1_ALLOWED_KEYS = {
    "kind", "head", "status", "covered", "verdict", "location",
    "new_path", "old_path", "new_line", "old_line",
}
_V2_KEYS = ("kind", "head", "covered", "verdict", "origin", "publication")
_HEAD = re.compile(r"[0-9a-f]{40}")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")


class MarkerKind(StrEnum):
    FINDING = "finding"
    SUMMARY = "summary"
    FOLLOWUP = "followup"


def _normalized_text(value: object, *, name: str, limit: int) -> str:
    if value is None:
        raise ValueError(f"{name} must not be null")
    result = unicodedata.normalize("NFC", str(value))
    if not result or len(result) > limit or any(unicodedata.category(char) == "Cc" for char in result):
        raise ValueError(f"{name} must be non-empty, at most {limit} characters and contain no controls")
    return result


def _normalized_id(value: object) -> str:
    return _normalized_text(value, name="opaque ID", limit=256)


def _sorted_ids(values: Iterable[object]) -> tuple[str, ...]:
    normalized = tuple(_normalized_id(value) for value in values)
    if not 1 <= len(normalized) <= 50 or len(set(normalized)) != len(normalized):
        raise ValueError("covered IDs must contain 1-50 unique values")
    return tuple(sorted(normalized, key=lambda value: value.encode("utf-8")))


class ReviewMarker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["v1", "v2"] = "v1"
    kind: MarkerKind
    head: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    status: Literal["complete", "complete_with_warnings", "complete_with_partial_recovery"] | None = None
    covered: tuple[str, ...] = ()
    verdict: Literal["fixed", "open", "clarify", "withdrawn"] | None = None
    location: Literal["fallback"] | None = None
    new_path: str | None = None
    old_path: str | None = None
    new_line: int | None = None
    old_line: int | None = None
    origin: str | None = None
    publication: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_ids(cls, values: object) -> object:
        if not isinstance(values, dict):
            return values
        result = dict(values)
        version = result.get("version", "v1")
        covered = tuple(result.get("covered") or ())
        if version == "v1":
            result["covered"] = tuple(str(UUID(str(value))) for value in covered)
        else:
            result["covered"] = tuple(_normalized_id(value) for value in covered)
            if result.get("origin") is not None:
                result["origin"] = _normalized_id(result["origin"])
        return result

    @model_validator(mode="after")
    def validate_shape(self) -> "ReviewMarker":
        if len(self.covered) > 50 or len(set(self.covered)) != len(self.covered):
            raise ValueError("covered IDs must be unique and contain at most 50 values")

        location_values = (self.new_path, self.old_path, self.new_line, self.old_line)
        if self.version == "v2":
            if (
                self.kind is not MarkerKind.FOLLOWUP
                or not self.covered
                or self.verdict is None
                or self.origin is None
                or self.publication is None
                or self.status is not None
                or self.location is not None
                or any(value is not None for value in location_values)
            ):
                raise ValueError("v2 marker requires only followup fields")
            if self.covered != tuple(sorted(self.covered, key=lambda value: value.encode("utf-8"))):
                raise ValueError("v2 covered IDs must use canonical order")
            return self

        if self.origin is not None or self.publication is not None:
            raise ValueError("v1 marker cannot contain v2 fields")
        if self.kind is MarkerKind.FOLLOWUP:
            if self.verdict is None or self.status is not None:
                raise ValueError("followup marker requires verdict and no status")
        elif self.kind is MarkerKind.SUMMARY:
            if self.status is None or self.verdict is not None:
                raise ValueError("summary marker requires status and no verdict")
        elif self.status is not None or self.verdict is not None or self.covered:
            raise ValueError("finding marker cannot contain status, verdict or covered IDs")

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


def _encode(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _decode(value: str) -> str:
    if not _BASE64URL.fullmatch(value):
        raise ValueError("value must be base64url")
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
    normalized = _normalized_id(decoded)
    if normalized != decoded or _encode(decoded) != value:
        raise ValueError("value must use canonical base64url and NFC")
    return decoded


def _decode_path(value: str) -> str:
    if not _BASE64URL.fullmatch(value):
        raise ValueError("path must be base64url")
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
    if _encode(decoded) != value:
        raise ValueError("path must use canonical base64url")
    return decoded


def render_marker(marker: ReviewMarker) -> str:
    if marker.version == "v2":
        fields = [
            "v2", "kind=followup", f"head={marker.head}",
            "covered=" + ",".join(_encode(item) for item in marker.covered),
            f"verdict={marker.verdict}", f"origin={_encode(marker.origin or '')}",
            f"publication={marker.publication}",
        ]
        return "<!-- ai-review:" + ";".join(fields) + " -->"

    fields = ["v1", f"kind={marker.kind}"]
    if marker.status:
        fields.append(f"status={marker.status}")
    fields.append(f"head={marker.head}")
    if marker.location:
        fields.extend([
            "location=fallback", f"new_path={_encode(marker.new_path or '')}",
            f"old_path={_encode(marker.old_path or '')}",
            f"new_line={'null' if marker.new_line is None else marker.new_line}",
            f"old_line={'null' if marker.old_line is None else marker.old_line}",
        ])
    if marker.covered:
        fields.append("covered=" + ",".join(marker.covered))
    if marker.verdict:
        fields.append(f"verdict={marker.verdict}")
    return "<!-- ai-review:" + ";".join(fields) + " -->"


def _parse_v1(parts: list[str]) -> ReviewMarker | None:
    raw: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            return None
        key, value = part.split("=", 1)
        if key not in _V1_ALLOWED_KEYS or key in raw or not value:
            return None
        raw[key] = value
    covered_raw = raw.get("covered")
    covered = () if covered_raw is None else tuple(covered_raw.split(","))
    if any(not item for item in covered):
        return None
    values: dict[str, object] = {
        "version": "v1", "kind": raw["kind"], "head": raw["head"], "covered": covered,
        "status": raw.get("status"), "verdict": raw.get("verdict"), "location": raw.get("location"),
    }
    location_keys = {"new_path", "old_path", "new_line", "old_line"}
    if values["location"]:
        if location_keys - raw.keys():
            return None
        values.update({
            "new_path": _decode_path(raw["new_path"]), "old_path": _decode_path(raw["old_path"]),
            "new_line": None if raw["new_line"] == "null" else int(raw["new_line"]),
            "old_line": None if raw["old_line"] == "null" else int(raw["old_line"]),
        })
    elif location_keys & raw.keys():
        return None
    return ReviewMarker.model_validate(values)


def _parse_v2(parts: list[str]) -> ReviewMarker | None:
    if len(parts) != len(_V2_KEYS):
        return None
    pairs = [part.split("=", 1) for part in parts]
    if any(len(pair) != 2 or not pair[1] for pair in pairs):
        return None
    if tuple(pair[0] for pair in pairs) != _V2_KEYS:
        return None
    raw = {key: value for key, value in pairs}
    if raw["kind"] != MarkerKind.FOLLOWUP:
        return None
    covered = tuple(_decode(value) for value in raw["covered"].split(","))
    return ReviewMarker(
        version="v2", kind=MarkerKind.FOLLOWUP, head=raw["head"], covered=covered,
        verdict=raw["verdict"], origin=_decode(raw["origin"]), publication=raw["publication"],
    )


def parse_marker(body: str, author_id: str | int | None, trusted_author_id: str | int) -> ReviewMarker | None:
    if str(author_id) != str(trusted_author_id):
        return None
    matches = _MARKER.findall(body)
    if len(matches) != 1 or len(_MARKER_LIKE.findall(body)) != 1:
        return None
    parts = matches[0].split(";")
    try:
        if parts[0] == "v1":
            return _parse_v1(parts[1:])
        if parts[0] == "v2":
            return _parse_v2(parts[1:])
    except (KeyError, TypeError, UnicodeError, ValueError):
        return None
    return None


def decorate_ai_message(text: str, marker: ReviewMarker) -> str:
    safe_text = _MARKER_LIKE.sub("&lt;!-- ai-review:", text)
    return f"{PREFIX}\n\n{safe_text}\n\n{render_marker(marker)}"


def _field(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack(">I", len(encoded)) + encoded


def publication_key(
    provider: VCSProvider | str,
    project: object,
    review_id: object,
    origin_thread_id: object,
    head: str,
    covered: Iterable[object],
) -> str:
    try:
        provider_name = VCSProvider(str(provider).upper()).value.lower()
    except ValueError as error:
        raise ValueError(f"unknown VCS provider: {provider}") from error
    project_text = _normalized_text(project, name="project", limit=512)
    review_text = _normalized_text(review_id, name="review ID", limit=256)
    origin_text = _normalized_id(origin_thread_id)
    if not _HEAD.fullmatch(head):
        raise ValueError("head must be 40 lowercase hexadecimal characters")
    covered_ids = _sorted_ids(covered)
    framed = bytearray(b"ai-review-publication-v2\0")
    for value in (provider_name, project_text, review_text, origin_text, head):
        framed.extend(_field(value))
    framed.extend(struct.pack(">I", len(covered_ids)))
    for value in covered_ids:
        framed.extend(_field(value))
    return hashlib.sha256(framed).hexdigest()
