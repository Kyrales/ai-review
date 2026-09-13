import hashlib
import os
from pathlib import Path

import pytest

from ai_review.services.knowledge.rules_file import (
    ConcurrentRulesUpdateError,
    InvalidRulesDocumentError,
    RulesDocument,
    RulesFileTransaction,
    RulesFileWriteError,
)


START = b"<!-- ai-review-knowledge:start -->"
END = b"<!-- ai-review-knowledge:end -->"


@pytest.mark.parametrize("bom", [b"", b"\xef\xbb\xbf"])
@pytest.mark.parametrize("eol", [b"\n", b"\r\n"])
def test_round_trip_preserves_bom_eol_and_manual_bytes(
    tmp_path: Path, bom: bytes, eol: bytes
) -> None:
    prefix = "# Ручные правила".encode() + eol + b"prefix\r\nkept\n" + START + eol
    suffix = END + eol + b"suffix\nkept\r\n"
    source = bom + prefix + eol + b"- old rule" + eol + eol + suffix
    path = tmp_path / "project-rules.md"
    path.write_bytes(source)

    document = RulesDocument.read(path)
    rendered = document.with_rules(["new rule"])

    assert rendered.startswith(bom + prefix)
    assert rendered.endswith(suffix)
    assert (b"- new rule" + eol) in rendered
    assert document.rules == ("old rule",)
    assert "old rule" not in document.manual_context_text()
    assert "Ручные правила" in document.manual_context_text()
    assert "suffix" in document.manual_context_text()


def test_missing_section_is_appended_without_changing_existing_bytes(tmp_path: Path) -> None:
    original = b"# Manual\r\nlast line without newline"
    path = tmp_path / "project-rules.md"
    path.write_bytes(original)

    rendered = RulesDocument.read(path).with_rules(["first"])

    expected_tail = (
        b"\r\n\r\n## "
        + "Автоматически накопленные правила".encode()
        + b"\r\n\r\n"
        + START
        + b"\r\n\r\n- first\r\n\r\n"
        + END
        + b"\r\n"
    )
    assert rendered == original + expected_tail


def test_empty_rules_render_one_empty_managed_section(tmp_path: Path) -> None:
    path = tmp_path / "project-rules.md"
    path.write_bytes(b"")

    rendered = RulesDocument.read(path).with_rules([])

    assert rendered == (
        "## Автоматически накопленные правила\n\n"
        "<!-- ai-review-knowledge:start -->\n\n"
        "<!-- ai-review-knowledge:end -->\n"
    ).encode()


@pytest.mark.parametrize(
    "body",
    [
        START + b"\n" + START + b"\n" + END + b"\n",
        START + b"\n" + END + b"\n" + END + b"\n",
        END + b"\n" + START + b"\n",
        START + b"\nmanual\n" + END + b"\n",
        START + b"\n  - nested\n" + END + b"\n",
        START + b"\n* wrong marker\n" + END + b"\n",
    ],
)
def test_malformed_markers_and_managed_lines_are_rejected(
    tmp_path: Path, body: bytes
) -> None:
    path = tmp_path / "project-rules.md"
    path.write_bytes(body)

    with pytest.raises(InvalidRulesDocumentError):
        RulesDocument.read(path)


def test_invalid_utf8_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "project-rules.md"
    path.write_bytes(b"manual\xff")

    with pytest.raises(InvalidRulesDocumentError, match="UTF-8"):
        RulesDocument.read(path)


@pytest.mark.parametrize(
    "rule",
    [
        "",
        "line\nbreak",
        "- nested",
        "1. nested",
        "contains <!-- comment -->",
        "contains ``` fence",
        "contains #ai-review-knowledge tag",
        "control\x01character",
        "e\u0301",
    ],
)
def test_new_rule_must_follow_single_line_grammar(tmp_path: Path, rule: str) -> None:
    path = tmp_path / "project-rules.md"
    path.write_text("manual", encoding="utf-8")

    with pytest.raises(ValueError):
        RulesDocument.read(path).with_rules([rule])


@pytest.mark.parametrize("rule", ["before\u2028after", "before\u2029after"])
def test_unicode_line_and_paragraph_separators_are_rejected(
    tmp_path: Path, rule: str
) -> None:
    path = tmp_path / "project-rules.md"
    path.write_text("manual", encoding="utf-8")

    with pytest.raises(ValueError, match="line separator"):
        RulesDocument.read(path).with_rules([rule])


@pytest.mark.parametrize("rule", ["before\ud800after", "before\udfffafter"])
def test_lone_surrogates_are_rejected_as_non_utf8(tmp_path: Path, rule: str) -> None:
    path = tmp_path / "project-rules.md"
    path.write_text("manual", encoding="utf-8")

    with pytest.raises(ValueError, match="UTF-8"):
        RulesDocument.read(path).with_rules([rule])


def test_rendered_document_self_reparses_with_identical_rules_and_manual_slices(
    tmp_path: Path,
) -> None:
    path = tmp_path / "project-rules.md"
    prefix = b"manual prefix\r\n" + START + b"\r\n"
    suffix = END + b"\r\nmanual suffix\n"
    path.write_bytes(prefix + b"\r\n- old\r\n\r\n" + suffix)
    document = RulesDocument.read(path)

    rendered = document.with_rules(["new rule"])
    path.write_bytes(rendered)
    reparsed = RulesDocument.read(path)

    assert reparsed.rules == ("new rule",)
    assert reparsed._manual_before == document._manual_before
    assert reparsed._manual_after == document._manual_after


def test_unified_diff_reports_only_actual_document_change(tmp_path: Path) -> None:
    path = tmp_path / "project-rules.md"
    path.write_text("manual\n", encoding="utf-8")
    document = RulesDocument.read(path)
    rendered = document.with_rules(["rule"])

    diff = document.unified_diff(rendered)

    assert diff.startswith("--- ")
    assert "+- rule" in diff
    assert document.unified_diff(document.original_bytes) == ""


def test_commit_rejects_stale_hash_and_keeps_original(tmp_path: Path) -> None:
    path = tmp_path / "project-rules.md"
    path.write_bytes(b"before")
    transaction = RulesFileTransaction(path)

    with pytest.raises(ConcurrentRulesUpdateError):
        transaction.commit("0" * 64, b"after")

    assert path.read_bytes() == b"before"
    assert not list(tmp_path.glob(".project-rules.md.*.tmp"))


def test_commit_is_atomic_and_noop_is_byte_identical(tmp_path: Path) -> None:
    path = tmp_path / "project-rules.md"
    original = b"same\r\nbytes"
    path.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()
    transaction = RulesFileTransaction(path)

    assert transaction.commit(digest, original) is False
    assert path.read_bytes() == original

    assert transaction.commit(digest, b"changed") is True
    assert path.read_bytes() == b"changed"
    assert not list(tmp_path.glob(".project-rules.md.*.tmp"))


def test_failed_replace_cleans_temp_and_keeps_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "project-rules.md"
    original = b"before"
    path.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()

    def fail_replace(source: str | bytes | Path, target: str | bytes | Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(RulesFileWriteError, match="replace failed"):
        RulesFileTransaction(path).commit(digest, b"after")

    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".project-rules.md.*.tmp"))


def test_failure_after_temp_creation_closes_and_removes_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "project-rules.md"
    original = b"before"
    path.write_bytes(original)
    digest = hashlib.sha256(original).hexdigest()

    def fail_chmod(target: str | bytes | Path, mode: int) -> None:
        raise OSError("chmod failed")

    monkeypatch.setattr(os, "chmod", fail_chmod)

    with pytest.raises(RulesFileWriteError, match="chmod failed"):
        RulesFileTransaction(path).commit(digest, b"after")

    assert path.read_bytes() == original
    assert not list(tmp_path.glob(".project-rules.md.*.tmp"))


@pytest.mark.parametrize("final_eol", [b"", b"\n", b"\r\n"])
def test_replacement_preserves_manual_final_newline_exactly(
    tmp_path: Path, final_eol: bytes
) -> None:
    path = tmp_path / "project-rules.md"
    original = START + b"\n\n- old\n\n" + END + final_eol
    path.write_bytes(original)

    rendered = RulesDocument.read(path).with_rules(["new"])

    assert rendered.endswith(END + final_eol)
