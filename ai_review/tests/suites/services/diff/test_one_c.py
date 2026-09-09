from ai_review.libs.config.review import ReviewMode
from ai_review.services.diff.one_c import (
    filter_role_restriction_templates,
    filter_role_restriction_templates_from_unified_diff,
    ignored_role_template_lines,
    is_false_bsl_multiline_comment_finding,
)
from ai_review.services.diff.schema import DiffFileSchema


def test_filter_removes_standard_template_definition_but_keeps_usage_and_custom_template():
    current = """<rights>
\t<restrictionTemplate>
\t\t<name>ПоЗначениям</name>
\t\t<condition>ТИПОВОЕ СОДЕРЖИМОЕ</condition>
\t</restrictionTemplate>
\t<restrictionTemplate>
\t\t<name>ПроектныйШаблон</name>
\t\t<condition>ПРОЕКТНОЕ СОДЕРЖИМОЕ</condition>
\t</restrictionTemplate>
\t<condition>#ПоЗначениям("Справочник.Проекты")</condition>
</rights>"""
    rendered = DiffFileSchema(
        file="src/cf/sppr/src/Roles/Test/Rights.rights",
        diff="\n".join(
            f"+{number}: {line}" for number, line in enumerate(current.splitlines(), 1)
        ),
        added_lines=set(range(1, 12)),
    )

    result = filter_role_restriction_templates(
        rendered,
        current=current,
        previous=None,
        names={"ПоЗначениям"},
        mode=ReviewMode.ADDED_AND_REMOVED_WITH_CONTEXT,
    )

    assert "ТИПОВОЕ СОДЕРЖИМОЕ" not in result.diff
    assert "<name>ПоЗначениям</name>" not in result.diff
    assert "ПРОЕКТНОЕ СОДЕРЖИМОЕ" in result.diff
    assert '#ПоЗначениям("Справочник.Проекты")' in result.diff
    assert result.added_lines == {1, 6, 7, 8, 9, 10, 11}


def test_filter_uses_previous_lines_for_removed_template_definition():
    previous = """<rights>
\t<restrictionTemplate>
\t\t<name>ПоЗначениямРасширенный</name>
\t\t<condition>СТАРОЕ ТИПОВОЕ СОДЕРЖИМОЕ</condition>
\t</restrictionTemplate>
</rights>"""
    rendered = DiffFileSchema(
        file="Roles/Test/Rights.rights",
        diff="\n".join(
            f"-{number}: {line}" for number, line in enumerate(previous.splitlines(), 1)
        ),
        added_lines=set(),
    )

    result = filter_role_restriction_templates(
        rendered,
        current="<rights>\n</rights>",
        previous=previous,
        names={"ПоЗначениямРасширенный"},
        mode=ReviewMode.ADDED_AND_REMOVED,
    )

    assert "СТАРОЕ ТИПОВОЕ СОДЕРЖИМОЕ" not in result.diff
    assert result.diff == "-1: <rights>\n-6: </rights>"


def test_filter_does_not_change_a_different_file_name():
    rendered = DiffFileSchema(
        file="Rights.example",
        diff="+1: <name>ПоЗначениям</name>",
        added_lines={1},
    )

    result = filter_role_restriction_templates(
        rendered,
        current="<name>ПоЗначениям</name>",
        previous=None,
        names={"ПоЗначениям"},
        mode=ReviewMode.FULL_FILE_CURRENT,
    )

    assert result == rendered


def test_filter_uses_previous_snapshot_for_full_file_previous_mode():
    previous = """<restrictionTemplate>
<name>ПоЗначениямИНаборамРасширенный</name>
<condition>ТИПОВОЕ СОДЕРЖИМОЕ</condition>
</restrictionTemplate>"""
    rendered = DiffFileSchema(
        file="Rights.rights",
        diff="\n".join(
            f" {number}: {line}" for number, line in enumerate(previous.splitlines(), 1)
        ),
        added_lines=set(),
    )

    result = filter_role_restriction_templates(
        rendered,
        current=None,
        previous=previous,
        names={"ПоЗначениямИНаборамРасширенный"},
        mode=ReviewMode.FULL_FILE_PREVIOUS,
    )

    assert result.diff == ""


def test_filter_removes_ignored_role_templates_from_unified_diff():
    previous = """<rights>
<restrictionTemplate>
<name>ПоЗначениям</name>
old standard
</restrictionTemplate>
outside old
</rights>"""
    current = """<rights>
<restrictionTemplate>
<name>ПоЗначениям</name>
new standard
</restrictionTemplate>
outside new
</rights>"""
    raw_diff = """diff --git a/Rights.rights b/Rights.rights
@@ -1,7 +1,7 @@
 <rights>
 <restrictionTemplate>
 <name>ПоЗначениям</name>
-old standard
+new standard
 </restrictionTemplate>
-outside old
+outside new
 </rights>"""

    result = filter_role_restriction_templates_from_unified_diff(
        raw_diff,
        file="src/Roles/Test/Rights.rights",
        current=current,
        previous=previous,
        names={"ПоЗначениям"},
    )

    assert "standard" not in result
    assert "outside old" in result
    assert "outside new" in result


def test_ignored_role_template_lines_scans_source_once(monkeypatch):
    source = """<restrictionTemplate>
<name>ПоЗначениям</name>
standard
</restrictionTemplate>"""
    calls = 0

    from ai_review.services.diff import one_c

    original = one_c._ignored_lines

    def counted(text, names):
        nonlocal calls
        calls += 1
        return original(text, names)

    monkeypatch.setattr(one_c, "_ignored_lines", counted)

    assert ignored_role_template_lines(
        source, file="Rights.rights", names={"ПоЗначениям"}
    ) == {1, 2, 3, 4}
    assert calls == 1


def test_detects_false_finding_for_comment_between_bsl_string_continuations():
    source = 'Text = "first\n|second\n// author note\n|third\n";'

    assert is_false_bsl_multiline_comment_finding(
        source,
        file="CommonModules/Test/Module.bsl",
        line=3,
        message="Комментарий разрывает многострочный строковый литерал",
    )
    assert not is_false_bsl_multiline_comment_finding(
        source,
        file="CommonModules/Test/Module.bsl",
        line=3,
        message="В комментарии опубликован секрет",
    )
    assert not is_false_bsl_multiline_comment_finding(
        source,
        file="CommonModules/Test/Module.bsl",
        line=3,
        message=(
            "Комментарий содержит секрет в продолжении многострочного литерала "
            "и создаёт разрыв доступа"
        ),
    )
    assert not is_false_bsl_multiline_comment_finding(
        source,
        file="CommonModules/Test/Module.bsl",
        line=3,
        message=(
            "Комментарий разрывает маскировку секрета рядом с многострочным "
            "строковым литералом"
        ),
    )
    assert is_false_bsl_multiline_comment_finding(
        source,
        file="CommonModules/Test/Module.bsl",
        line=3,
        message=(
            "Комментарий внутри многострочного строкового литерала разрывает "
            "его продолжение"
        ),
    )


def test_does_not_hide_finding_when_string_was_closed_before_comment():
    source = 'Text = "first\n|second";\n// author note\n|third'

    assert not is_false_bsl_multiline_comment_finding(
        source,
        file="CommonModules/Test/Module.bsl",
        line=3,
        message="Комментарий разрывает многострочный строковый литерал",
    )
