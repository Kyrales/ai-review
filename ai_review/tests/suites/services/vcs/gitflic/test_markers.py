from uuid import UUID

from ai_review.services.vcs.gitflic.markers import (
    MarkerKind,
    ReviewMarker,
    decorate_ai_message,
    parse_marker,
    render_marker,
)


HEAD = "a" * 40
OWNER = "owner-id"


def marker(**values: object) -> ReviewMarker:
    return ReviewMarker(kind=values.pop("kind", MarkerKind.FINDING), head=HEAD, **values)


def test_marker_requires_trusted_author() -> None:
    body = f"<!-- ai-review:v1;kind=summary;status=complete;head={HEAD} -->"

    assert parse_marker(body, "other", OWNER) is None


def test_render_and_parse_followup_marker() -> None:
    covered = UUID("12345678-1234-1234-1234-123456789abc")
    original = marker(kind=MarkerKind.FOLLOWUP, covered=(covered,), verdict="fixed")

    rendered = render_marker(original)
    parsed = parse_marker(rendered, OWNER, OWNER)

    assert parsed == original


def test_marker_rejects_unknown_duplicate_and_invalid_values() -> None:
    cases = [
        f"<!-- ai-review:v1;kind=finding;head={HEAD};unknown=x -->",
        f"<!-- ai-review:v1;kind=finding;kind=summary;head={HEAD} -->",
        "<!-- ai-review:v2;kind=finding;head=" + HEAD + " -->",
        "<!-- ai-review:v1;kind=finding;head=not-a-sha -->",
        f"<!-- ai-review:v1;kind=followup;head={HEAD};covered=not-a-uuid;verdict=fixed -->",
    ]

    assert all(parse_marker(case, OWNER, OWNER) is None for case in cases)


def test_marker_rejects_more_than_fifty_or_duplicate_covered_ids() -> None:
    duplicate = "12345678-1234-1234-1234-123456789abc"
    repeated = ",".join([duplicate] * 2)
    too_many = ",".join(f"00000000-0000-0000-0000-{number:012d}" for number in range(51))

    assert parse_marker(
        f"<!-- ai-review:v1;kind=followup;head={HEAD};covered={repeated};verdict=fixed -->",
        OWNER,
        OWNER,
    ) is None
    assert parse_marker(
        f"<!-- ai-review:v1;kind=followup;head={HEAD};covered={too_many};verdict=fixed -->",
        OWNER,
        OWNER,
    ) is None


def test_model_marker_is_escaped_and_runtime_marker_is_unique() -> None:
    result = decorate_ai_message("text <!-- ai-review:v1;kind=summary -->", marker())

    assert result.startswith("🤖 **AI-ревьювер**")
    assert result.count("<!-- ai-review:v1;") == 1
    assert "&lt;!-- ai-review:v1;kind=summary -->" in result


def test_fallback_location_round_trips_base64url_paths() -> None:
    original = marker(
        location="fallback",
        new_path="src/cf/СППР/новый.bsl",
        old_path="src/cf/СППР/старый.bsl",
        new_line=None,
        old_line=17,
    )

    assert parse_marker(render_marker(original), OWNER, OWNER) == original
