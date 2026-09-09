import re
from pathlib import PurePosixPath
from xml.etree import ElementTree

from ai_review.libs.config.review import ReviewMode
from ai_review.services.diff.schema import DiffFileSchema

_BLOCK_START = re.compile(r"<restrictionTemplate(?:\s|>)")
_BLOCK_END = re.compile(r"</restrictionTemplate\s*>")
_NAME = re.compile(r"<name>\s*([^<]+?)\s*</name>")
_RENDERED_LINE = re.compile(r"^([ +\-])(\d+):")
_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_FALSE_BSL_MULTILINE_COMMENT_CLAIM = re.compile(
    r"(?:"
    r"комментари\w*.{0,80}(?:разрыва\w*|прерыва\w*)\s+"
    r"(?:многостроч\w*\s+(?:строков\w*\s+)?литерал\w*|его\s+продолжени\w*)"
    r"|"
    r"комментари\w*.{0,40}(?:внутри|между).{0,40}"
    r"многостроч\w*\s+(?:строков\w*\s+)?литерал\w*.{0,80}"
    r"(?:разрыва\w*|прерыва\w*)\s+его\s+продолжени\w*"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_ROLE_HAS_NO_RIGHTS_CLAIM = re.compile(
    r"роль.{0,80}(?:не\s+содержит|не\s+имеет|нет).{0,40}прав",
    re.IGNORECASE | re.DOTALL,
)
_FORM_CROSS_SCOPE_ID_CLAIM = re.compile(
    r"идентификатор.{0,80}элемент\w*\s+форм\w*.{0,80}"
    r"совпада\w*.{0,80}идентификатор\w*\s+атрибут\w*",
    re.IGNORECASE | re.DOTALL,
)


def is_false_1c_role_missing_rights_finding(
    rights: str | None, *, file: str, message: str
) -> bool:
    """Reject a role-level claim of no rights when its Rights.rights grants any."""
    path = PurePosixPath(file.replace("\\", "/"))
    if (
        not rights
        or path.suffix.lower() != ".mdo"
        or path.parent.parent.name != "Roles"
        or path.stem != path.parent.name
        or not _ROLE_HAS_NO_RIGHTS_CLAIM.search(message)
    ):
        return False
    return bool(
        re.search(
            r"<object(?:\s|>).*?<right(?:\s|>).*?<value>\s*true\s*</value>",
            rights,
            re.IGNORECASE | re.DOTALL,
        )
    )


def is_false_1c_form_cross_scope_id_finding(
    source: str | None, *, file: str, message: str
) -> bool:
    """Reject claims that item and attribute IDs share one forbidden namespace."""
    if (
        not source
        or PurePosixPath(file.replace("\\", "/")).name != "Form.form"
        or not _FORM_CROSS_SCOPE_ID_CLAIM.search(message)
    ):
        return False
    try:
        root = ElementTree.fromstring(source)
    except ElementTree.ParseError:
        return False

    def local_name(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    ids: dict[str, set[str]] = {"items": set(), "attributes": set()}
    for child in root:
        kind = local_name(child.tag)
        if kind not in ids:
            continue
        ids[kind].update(
            (node.text or "").strip()
            for node in child.iter()
            if local_name(node.tag) == "id" and (node.text or "").strip()
        )
    return bool(ids["items"] & ids["attributes"])


def is_false_bsl_multiline_comment_finding(
    source: str | None, *, file: str, line: int | None, message: str
) -> bool:
    """Recognize the known false claim that a BSL comment breaks a string."""
    if not source or line is None or PurePosixPath(file).suffix.lower() != ".bsl":
        return False
    if not _FALSE_BSL_MULTILINE_COMMENT_CLAIM.search(message):
        return False

    lines = source.splitlines()
    index = line - 1
    if index < 0 or index >= len(lines) or not lines[index].lstrip().startswith("//"):
        return False

    in_string = False
    for source_line in lines[:index]:
        text = source_line.lstrip()
        if text.startswith("//"):
            continue
        position = 0
        while position < len(source_line):
            if not in_string and source_line.startswith("//", position):
                break
            if source_line[position] != '"':
                position += 1
                continue
            if in_string and position + 1 < len(source_line) and source_line[position + 1] == '"':
                position += 2
                continue
            in_string = not in_string
            position += 1

    if not in_string:
        return False

    for source_line in lines[index + 1 :]:
        text = source_line.strip()
        if text and not text.startswith("//"):
            return text.startswith("|")
    return False


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
