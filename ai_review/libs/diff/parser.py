import re

from ai_review.libs.diff.models import (
    Diff,
    DiffFile,
    DiffHunk,
    DiffLine,
    DiffLineType,
    DiffRange,
    FileMode,
)
from ai_review.libs.diff.tools import is_source_line, get_line_type

HUNK_RE = re.compile(r"@@ -(\d+),?(\d+)? \+(\d+),?(\d+)? @@ ?(.*)?")
OLD_FILE_PREFIX = "--- "
NEW_FILE_PREFIX = "+++ "


def decode_git_path(value: str) -> str:
    """Decode a path from Git's C-quoted patch format and remove the a/b prefix."""
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        source = value[1:-1]
        decoded = bytearray()
        escapes = {
            "a": 0x07,
            "b": 0x08,
            "t": 0x09,
            "n": 0x0A,
            "v": 0x0B,
            "f": 0x0C,
            "r": 0x0D,
            '"': 0x22,
            "\\": 0x5C,
        }
        index = 0
        while index < len(source):
            char = source[index]
            if char != "\\":
                decoded.extend(char.encode("utf-8"))
                index += 1
                continue

            index += 1
            if index >= len(source):
                decoded.append(0x5C)
                break
            if source[index] in "01234567":
                end = index
                while end < min(index + 3, len(source)) and source[end] in "01234567":
                    end += 1
                decoded.append(int(source[index:end], 8))
                index = end
                continue

            decoded.append(escapes.get(source[index], ord(source[index])))
            index += 1

        value = decoded.decode("utf-8", errors="surrogateescape")

    if value.startswith("a/") or value.startswith("b/"):
        return value[2:]
    return value


class DiffParser:
    @classmethod
    def parse(cls, diff_string: str) -> Diff:
        lines = diff_string.split("\n")
        files: list[DiffFile] = []

        current_file: DiffFile | None = None
        current_hunk: DiffHunk | None = None

        added_count = removed_count = 0
        diff_pos = 0

        for raw in lines:
            diff_pos += 1

            # Начало нового файла
            if raw.startswith("diff "):
                current_file = DiffFile(
                    header=raw,
                    mode=FileMode.MODIFIED,
                    orig_name="",
                    new_name="",
                    hunks=[],
                )
                files.append(current_file)
                continue

            # Дополняем header файла
            if raw.startswith("index ") or raw.startswith("--- ") or raw.startswith("+++ "):
                current_file.header += "\n" + raw

            if raw.startswith(OLD_FILE_PREFIX) and raw != "--- /dev/null":
                current_file.orig_name = decode_git_path(raw[len(OLD_FILE_PREFIX):])
                continue

            if raw.startswith(NEW_FILE_PREFIX) and raw != "+++ /dev/null":
                current_file.new_name = decode_git_path(raw[len(NEW_FILE_PREFIX):])
                continue

            if raw == "+++ /dev/null":
                current_file.mode = FileMode.DELETED
                continue

            if raw == "--- /dev/null":
                current_file.mode = FileMode.NEW
                continue

            if raw.startswith("@@ "):
                match = HUNK_RE.match(raw)
                if not match:
                    raise ValueError(f"Invalid hunk header: {raw}")

                a, b, c, d, header = match.groups()
                orig_start, orig_len = int(a), int(b or 0)
                new_start, new_len = int(c), int(d or 0)

                current_hunk = DiffHunk(
                    header=header or "",
                    orig_range=DiffRange(orig_start, orig_len, []),
                    new_range=DiffRange(new_start, new_len, []),
                    lines=[],
                )
                current_file.hunks.append(current_hunk)

                added_count, removed_count = new_start, orig_start
                continue

            if current_hunk and is_source_line(raw):
                line_type = get_line_type(raw)
                content = raw[1:]

                if line_type is DiffLineType.ADDED:
                    line = DiffLine(line_type, added_count, content, diff_pos)
                    current_hunk.new_range.lines.append(line)
                    current_hunk.lines.append(line)
                    added_count += 1

                elif line_type is DiffLineType.REMOVED:
                    line = DiffLine(line_type, removed_count, content, diff_pos)
                    current_hunk.orig_range.lines.append(line)
                    current_hunk.lines.append(line)
                    removed_count += 1

                else:
                    line_new = DiffLine(DiffLineType.UNCHANGED, added_count, content, diff_pos)
                    line_old = DiffLine(DiffLineType.UNCHANGED, removed_count, content, diff_pos)
                    current_hunk.new_range.lines.append(line_new)
                    current_hunk.orig_range.lines.append(line_old)
                    current_hunk.lines.append(line_new)
                    added_count += 1
                    removed_count += 1

        return Diff(files=files, raw=diff_string)
