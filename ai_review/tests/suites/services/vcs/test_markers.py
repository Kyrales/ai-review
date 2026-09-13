import base64
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_review.services.vcs.markers import (
    MarkerKind,
    ReviewMarker,
    parse_marker,
    publication_key,
    render_marker,
)


OWNER = "ai-owner"
VECTORS = json.loads(
    (
        Path(__file__).parents[3]
        / "fixtures"
        / "services"
        / "vcs"
        / "marker_vectors.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("vector", VECTORS["markers"], ids=lambda vector: vector["name"])
def test_marker_golden_vectors_round_trip(vector: dict[str, object]) -> None:
    expected = ReviewMarker.model_validate(vector["model"])

    assert render_marker(expected) == vector["wire"]
    assert parse_marker(vector["wire"], OWNER, OWNER) == expected


@pytest.mark.parametrize("vector", VECTORS["opaque_contract"], ids=lambda vector: vector["name"])
def test_opaque_id_contract_matches_cross_language_vectors(vector: dict[str, object]) -> None:
    wire = vector.get("wire")
    if wire is None:
        value = str(vector["repeat"]) * int(vector["repeat_count"])
        encoded = base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")
        wire = (
            f"<!-- ai-review:v2;kind=followup;head={'a' * 40};covered={encoded};"
            f"verdict=open;origin=dGhyZWFk;publication={'b' * 64} -->"
        )

    marker = parse_marker(str(wire), OWNER, OWNER)

    assert marker is not None
    if "covered" in vector:
        assert marker.covered == tuple(vector["covered"])
    if "code_points" in vector:
        assert len(marker.covered[0]) == vector["code_points"]


@pytest.mark.parametrize("vector", VECTORS["publications"], ids=lambda vector: vector["provider"])
def test_publication_key_matches_golden_binary_framing(vector: dict[str, object]) -> None:
    inputs = {key: value for key, value in vector.items() if key != "expected"}

    assert publication_key(**inputs) == vector["expected"]


def test_v2_rejects_noncanonical_wire_forms() -> None:
    head = "a" * 40
    publication = "b" * 64
    valid_fields = (
        f"kind=followup;head={head};covered=YQ;verdict=open;"
        f"origin=dGhyZWFk;publication={publication}"
    )
    invalid = [
        valid_fields.replace("kind=followup", "kind=summary"),
        valid_fields.replace("covered=YQ", "covered="),
        valid_fields.replace("covered=YQ", "covered=YQ,YQ"),
        valid_fields.replace("covered=YQ", "covered=eg,YQ"),
        valid_fields.replace("covered=YQ", "covered=YQ=="),
        valid_fields.replace("covered=YQ", "covered=YQ;extra=x"),
        valid_fields.replace(f"head={head};covered=YQ", f"covered=YQ;head={head}"),
        valid_fields.replace("origin=dGhyZWFk;", ""),
        valid_fields.replace(f";publication={publication}", ""),
        valid_fields.replace("covered=YQ", "covered=YQpi"),
        valid_fields.replace("covered=YQ", "covered=ZcyB"),
    ]

    assert all(
        parse_marker(f"<!-- ai-review:v2;{fields} -->", OWNER, OWNER) is None
        for fields in invalid
    )


def test_v2_rejects_more_than_fifty_covered_ids() -> None:
    covered = ",".join(
        base64.urlsafe_b64encode(f"id-{number:03d}".encode()).decode().rstrip("=")
        for number in range(51)
    )
    body = (
        f"<!-- ai-review:v2;kind=followup;head={'a' * 40};covered={covered};"
        f"verdict=open;origin=dGhyZWFk;publication={'b' * 64} -->"
    )

    assert parse_marker(body, OWNER, OWNER) is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider", "unknown"),
        ("project", ""),
        ("project", "bad\nproject"),
        ("project", "p" * 513),
        ("review_id", "r" * 257),
        ("origin_thread_id", "thread\u0000id"),
        ("origin_thread_id", None),
        ("head", "A" * 40),
        ("covered", []),
        ("covered", ["same", "same"]),
    ],
)
def test_publication_key_rejects_invalid_inputs(field: str, value: object) -> None:
    inputs: dict[str, object] = {
        "provider": "gitflic",
        "project": "owner/project",
        "review_id": "60",
        "origin_thread_id": "thread-1",
        "head": "a" * 40,
        "covered": ["reply-1"],
    }
    inputs[field] = value

    with pytest.raises(ValueError):
        publication_key(**inputs)


def test_v1_keeps_long_fallback_path_compatibility() -> None:
    path = "src/" + "a" * 300
    original = ReviewMarker(
        kind=MarkerKind.FINDING,
        head="a" * 40,
        location="fallback",
        new_path=path,
        old_path=path,
        new_line=None,
        old_line=1,
    )

    assert parse_marker(render_marker(original), OWNER, OWNER) == original


def test_v2_model_stores_normalized_opaque_ids_as_strings() -> None:
    marker = ReviewMarker(
        version="v2",
        kind=MarkerKind.FOLLOWUP,
        head="a" * 40,
        covered=("e\u0301",),
        verdict="withdrawn",
        origin="thread/e\u0301",
        publication="b" * 64,
    )

    assert marker.covered == ("é",)
    assert marker.origin == "thread/é"


def test_v2_model_requires_sorted_unique_covered_ids() -> None:
    with pytest.raises(ValidationError):
        ReviewMarker(
            version="v2",
            kind=MarkerKind.FOLLOWUP,
            head="a" * 40,
            covered=("z", "a"),
            verdict="open",
            origin="thread",
            publication="b" * 64,
        )
