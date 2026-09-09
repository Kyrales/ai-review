import re
from pathlib import PurePosixPath

from ai_review.libs.config.review import ReviewMode
from ai_review.services.diff.schema import DiffFileSchema

_BLOCK_START = re.compile(r"<restrictionTemplate(?:\s|>)")
_BLOCK_END = re.compile(r"</restrictionTemplate\s*>")
_NAME = re.compile(r"<name>\s*([^<]+?)\s*</name>")
_RENDERED_LINE = re.compile(r"^([ +\-])(\d+):")
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _ignored_lines(source: str | None, names: set[str]) -> set[int]:
    if not source or not names:
        return set()

    ignored: set[int] = set()
    start: int | None = None
    name: str | None = None
    for number, line in enumerate(source.splitlines(), 1):
        if start is None:
            if _BLOCK_START.search(line):
                start = number
                match = _NAME.search(line)
                name = match.group(1) if match else None
        else:
            match = _NAME.search(line)
            if match:
                name = match.group(1)

        if start is not None and _BLOCK_END.search(line):
            if name in names:
                ignored.update(range(start, number + 1))
            start = None
            name = None
    return ignored


def is_ignored_role_template_line(
    source: str | None, *, file: str, line: int | None, names: set[str]
) -> bool:
    return line is not None and line in ignored_role_template_lines(
        source, file=file, names=names
    )


def ignored_role_template_lines(
    source: str | None, *, file: str, names: set[str]
) -> set[int]:
    path = file.replace("\\", "/")
    if PurePosixPath(path).name != "Rights.rights":
        return set()
    return _ignored_lines(source, names)


def filter_role_restriction_templates_from_unified_diff(
    raw_diff: str,
    *,
    file: str,
    current: str | None,
    previous: str | None,
    names: set[str],
) -> str:
    """Remove configured template definitions from a model-visible unified diff."""
    path = file.replace("\\", "/")
    if PurePosixPath(path).name != "Rights.rights" or not names:
        return raw_diff

    current_ignored = _ignored_lines(current, names)
    previous_ignored = _ignored_lines(previous, names)
    old_line: int | None = None
    new_line: int | None = None
    kept: list[str] = []
    for text in raw_diff.splitlines():
        hunk = _HUNK_HEADER.match(text)
        if hunk:
            old_line, new_line = map(int, hunk.groups())
            kept.append(text)
            continue
        if old_line is None or new_line is None or text.startswith(("+++", "---")):
            kept.append(text)
            continue

        marker = text[:1]
        ignored = False
        if marker == "+":
            ignored = new_line in current_ignored
            new_line += 1
        elif marker == "-":
            ignored = old_line in previous_ignored
            old_line += 1
        elif marker == " ":
            ignored = old_line in previous_ignored or new_line in current_ignored
            old_line += 1
            new_line += 1
        if not ignored:
            kept.append(text)
    return "\n".join(kept)


def filter_role_restriction_templates(
    rendered: DiffFileSchema,
    *,
    current: str | None,
    previous: str | None,
    names: set[str],
    mode: ReviewMode,
) -> DiffFileSchema:
    """Remove selected 1C role template definitions from model-visible text."""
    path = rendered.file.replace("\\", "/")
    if PurePosixPath(path).name != "Rights.rights" or not names:
        return rendered

    current_ignored = _ignored_lines(current, names)
    previous_ignored = _ignored_lines(previous, names)
    kept: list[str] = []
    for line in rendered.diff.splitlines():
        match = _RENDERED_LINE.match(line)
        if not match:
            kept.append(line)
            continue
        marker, raw_number = match.groups()
        number = int(raw_number)
        use_previous = marker == "-" or (
            marker == " " and mode is ReviewMode.FULL_FILE_PREVIOUS
        )
        ignored = previous_ignored if use_previous else current_ignored
        if number not in ignored:
            kept.append(line)

    return rendered.model_copy(
        update={
            "diff": "\n".join(kept),
            "added_lines": rendered.added_lines - current_ignored,
        }
    )
