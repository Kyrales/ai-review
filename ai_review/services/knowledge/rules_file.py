from __future__ import annotations

import difflib
import hashlib
import os
import re
import stat
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path


UTF8_BOM = b"\xef\xbb\xbf"
START_MARKER = "<!-- ai-review-knowledge:start -->"
END_MARKER = "<!-- ai-review-knowledge:end -->"
SECTION_HEADING = "## Автоматически накопленные правила"
_START_BYTES = START_MARKER.encode()
_END_BYTES = END_MARKER.encode()
_LIST_MARKER_RE = re.compile(r"^(?:[-+*]\s|\d+[.)]\s)")


class InvalidRulesDocumentError(ValueError):
    pass


class ConcurrentRulesUpdateError(RuntimeError):
    pass


class RulesFileWriteError(OSError):
    pass


def validate_rule(rule: str) -> None:
    if not rule or rule != rule.strip() or len(rule) > 2000:
        raise ValueError("rule must be a non-empty trimmed line up to 2000 characters")
    if unicodedata.normalize("NFC", rule) != rule:
        raise ValueError("rule must use NFC normalization")
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in rule):
        raise ValueError("rule must not contain control characters")
    if "\u2028" in rule or "\u2029" in rule:
        raise ValueError("rule must not contain a Unicode line separator")
    try:
        rule.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise ValueError("rule must be valid UTF-8") from error
    if _LIST_MARKER_RE.match(rule):
        raise ValueError("rule must not start with a Markdown list marker")
    if "<!--" in rule or "-->" in rule:
        raise ValueError("rule must not contain HTML comments")
    if "```" in rule or "~~~" in rule:
        raise ValueError("rule must not contain fenced code blocks")
    if "#ai-review-knowledge" in rule:
        raise ValueError("rule must not contain the knowledge tag")


def _line_end(data: bytes, offset: int) -> int:
    if data[offset : offset + 2] == b"\r\n":
        return offset + 2
    if data[offset : offset + 1] == b"\n":
        return offset + 1
    return offset


def _is_line_start(data: bytes, offset: int, bom_size: int) -> bool:
    return offset == bom_size or (offset > bom_size and data[offset - 1] == 0x0A)


def _dominant_eol(data: bytes) -> bytes:
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    if crlf == lf and crlf:
        first_lf = data.find(b"\n")
        return b"\r\n" if first_lf > 0 and data[first_lf - 1] == 0x0D else b"\n"
    return b"\r\n" if crlf > lf else b"\n"


@dataclass(frozen=True)
class RulesDocument:
    path: Path
    original_bytes: bytes
    rules: tuple[str, ...]
    _eol: bytes
    _bom_size: int
    _body_start: int | None
    _body_end: int | None
    _manual_before: bytes
    _manual_after: bytes

    @classmethod
    def read(cls, path: str | os.PathLike[str]) -> RulesDocument:
        file_path = Path(path)
        return cls._from_bytes(file_path, file_path.read_bytes())

    @classmethod
    def _from_bytes(cls, file_path: Path, data: bytes) -> RulesDocument:
        bom_size = len(UTF8_BOM) if data.startswith(UTF8_BOM) else 0
        try:
            data[bom_size:].decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:
            raise InvalidRulesDocumentError("rules file must be strict UTF-8") from error

        start_count = data.count(_START_BYTES)
        end_count = data.count(_END_BYTES)
        if start_count != end_count or start_count > 1:
            raise InvalidRulesDocumentError("rules file must contain zero or one marker pair")

        eol = _dominant_eol(data[bom_size:])
        if start_count == 0:
            return cls(
                file_path,
                data,
                (),
                eol,
                bom_size,
                None,
                None,
                data[bom_size:],
                b"",
            )

        start = data.find(_START_BYTES)
        end = data.find(_END_BYTES)
        start_marker_end = start + len(_START_BYTES)
        end_marker_end = end + len(_END_BYTES)
        if (
            start >= end
            or not _is_line_start(data, start, bom_size)
            or not _is_line_start(data, end, bom_size)
            or _line_end(data, start_marker_end) == start_marker_end
            or (
                end_marker_end != len(data)
                and _line_end(data, end_marker_end) == end_marker_end
            )
        ):
            raise InvalidRulesDocumentError("managed markers must be standalone and ordered")

        body_start = _line_end(data, start_marker_end)
        body = data[body_start:end]
        try:
            body_text = body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as error:  # pragma: no cover - full file decoded above
            raise InvalidRulesDocumentError("rules file must be strict UTF-8") from error

        rules: list[str] = []
        for line in body_text.splitlines():
            if not line:
                continue
            if not line.startswith("- "):
                raise InvalidRulesDocumentError(
                    "managed section must contain only top-level '- ' rule lines"
                )
            rule = line[2:]
            try:
                validate_rule(rule)
            except ValueError as error:
                raise InvalidRulesDocumentError(str(error)) from error
            rules.append(rule)

        after_end = _line_end(data, end_marker_end)
        return cls(
            file_path,
            data,
            tuple(rules),
            eol,
            bom_size,
            body_start,
            end,
            data[bom_size:start],
            data[after_end:],
        )

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.original_bytes).hexdigest()

    def manual_context_text(self) -> str:
        before = self._manual_before.decode("utf-8", errors="strict")
        after = self._manual_after.decode("utf-8", errors="strict")
        if before and after and not before.endswith(("\n", "\r")):
            return before + self._eol.decode() + after
        return before + after

    def with_rules(self, lines: list[str] | tuple[str, ...]) -> bytes:
        for rule in lines:
            _validate_rule(rule)
        body = self._eol + b"".join(
            b"- " + rule.encode("utf-8") + self._eol for rule in lines
        )
        if lines:
            body += self._eol

        if self._body_start is not None and self._body_end is not None:
            rendered = (
                self.original_bytes[: self._body_start]
                + body
                + self.original_bytes[self._body_end :]
            )
        else:
            content = self.original_bytes[self._bom_size :]
            if not content:
                separator = b""
            elif content.endswith(self._eol * 2):
                separator = b""
            elif content.endswith(self._eol):
                separator = self._eol
            else:
                separator = self._eol * 2
            section = (
                SECTION_HEADING.encode("utf-8")
                + self._eol * 2
                + _START_BYTES
                + self._eol
                + body
                + _END_BYTES
                + self._eol
            )
            rendered = self.original_bytes + separator + section

        reparsed = type(self)._from_bytes(self.path, rendered)
        if reparsed.rules != tuple(lines):
            raise InvalidRulesDocumentError("rendered rules did not self-reparse")
        if self._body_start is not None and (
            reparsed._manual_before != self._manual_before
            or reparsed._manual_after != self._manual_after
        ):
            raise InvalidRulesDocumentError("rendering changed manual byte slices")
        return rendered

    def unified_diff(self, new_bytes: bytes) -> str:
        if new_bytes == self.original_bytes:
            return ""
        old = self.original_bytes.decode("utf-8-sig", errors="strict").splitlines(
            keepends=True
        )
        new = new_bytes.decode("utf-8-sig", errors="strict").splitlines(keepends=True)
        return "".join(
            difflib.unified_diff(
                old,
                new,
                fromfile=str(self.path),
                tofile=str(self.path),
            )
        )


class RulesFileTransaction:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def commit(self, expected_sha256: str, new_bytes: bytes) -> bool:
        temp_path: Path | None = None
        try:
            current = self.path.read_bytes()
        except OSError as error:
            raise RulesFileWriteError(str(error)) from error
        if hashlib.sha256(current).hexdigest() != expected_sha256:
            raise ConcurrentRulesUpdateError("rules file changed before commit")
        if current == new_bytes:
            return False

        try:
            fd, temp_name = tempfile.mkstemp(
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
            )
            temp_path = Path(temp_name)
            with os.fdopen(fd, "wb") as stream:
                os.chmod(temp_path, stat.S_IMODE(self.path.stat().st_mode))
                stream.write(new_bytes)
            os.replace(temp_path, self.path)
            temp_path = None
        except OSError as error:
            raise RulesFileWriteError(str(error)) from error
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass
        return True
