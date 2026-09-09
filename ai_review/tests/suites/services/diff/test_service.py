import pytest

from ai_review import config
from ai_review.libs.config.review import ReviewMode
from ai_review.libs.diff.models import Diff, DiffFile, FileMode
from ai_review.services.diff.service import DiffService
from ai_review.tests.fixtures.services.git import FakeGitService


@pytest.fixture
def fake_diff_file() -> DiffFile:
    return DiffFile(
        header="diff --git a/x b/x",
        mode=FileMode.MODIFIED,
        orig_name="a/x",
        new_name="b/x",
        hunks=[],
    )


@pytest.fixture
def fake_diff(fake_diff_file: DiffFile) -> Diff:
    return Diff(files=[fake_diff_file], raw="raw-diff")


def test_parse_empty_returns_empty_diff():
    diff = DiffService.parse("")
    assert diff.files == []
    assert diff.raw == ""


def test_parse_nonempty(monkeypatch: pytest.MonkeyPatch, fake_diff: Diff):
    monkeypatch.setattr(
        "ai_review.services.diff.service.DiffParser.parse", lambda _: fake_diff
    )
    diff = DiffService.parse("something")
    assert diff.files[0].new_name == "b/x"


@pytest.mark.parametrize(
    "mode,expected_prefix",
    [
        (ReviewMode.FULL_FILE_CURRENT, "# Failed to read current snapshot"),
        (ReviewMode.FULL_FILE_PREVIOUS, "# Failed to read previous snapshot"),
        (ReviewMode.FULL_FILE_DIFF, "# No matching lines for mode"),
        (ReviewMode.ONLY_ADDED, "# No matching lines for mode"),
        (ReviewMode.ONLY_REMOVED, "# No matching lines for mode"),
        (ReviewMode.ADDED_AND_REMOVED, "# No matching lines for mode"),
        (ReviewMode.ONLY_ADDED_WITH_CONTEXT, "# No matching lines for mode"),
        (ReviewMode.ONLY_REMOVED_WITH_CONTEXT, "# No matching lines for mode"),
        (ReviewMode.ADDED_AND_REMOVED_WITH_CONTEXT, "# No matching lines for mode"),
    ],
)
def test_render_file_routes_to_right_builder(
    mode: ReviewMode,
    fake_diff: Diff,
    monkeypatch: pytest.MonkeyPatch,
    expected_prefix: str,
):
    monkeypatch.setattr(
        "ai_review.services.diff.service.DiffParser.parse", lambda _: fake_diff
    )
    monkeypatch.setattr(config.settings.review, "mode", mode)

    out = DiffService.render_file(raw_diff="fake", file="b/x")
    assert out.file == "b/x"
    assert out.diff.startswith(expected_prefix)


def test_render_file_returns_unsupported(
    monkeypatch: pytest.MonkeyPatch, fake_diff: Diff
):
    monkeypatch.setattr(
        "ai_review.services.diff.service.DiffParser.parse", lambda _: fake_diff
    )
    monkeypatch.setattr(config.settings.review, "mode", "NON_EXISTING")
    out = DiffService.render_file(raw_diff="fake", file="b/x")
    assert out.file == "b/x"
    assert "# Unsupported mode" in out.diff


def test_render_files_invokes_render_file(
    fake_diff: Diff,
    monkeypatch: pytest.MonkeyPatch,
    fake_git_service: FakeGitService,
) -> None:
    monkeypatch.setattr(
        "ai_review.services.diff.service.DiffParser.parse", lambda _: fake_diff
    )
    monkeypatch.setattr(config.settings.review, "mode", ReviewMode.FULL_FILE_DIFF)

    fake_git_service.responses["get_diff_for_file"] = "fake-diff"

    out = DiffService.render_files(
        git=fake_git_service, base_sha="A", head_sha="B", files=["b/x"]
    )
    assert out
    assert out[0].file == "b/x"
    assert out[0].diff.startswith("# No matching lines for mode")


def test_render_file_applies_configured_1c_role_template_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_diff = """diff --git a/Roles/Test/Rights.rights b/Roles/Test/Rights.rights
--- /dev/null
+++ b/Roles/Test/Rights.rights
@@ -0,0 +1,6 @@
+<rights>
+<restrictionTemplate>
+<name>ПоЗначениям</name>
+<condition>ТИПОВОЕ СОДЕРЖИМОЕ</condition>
+</restrictionTemplate>
+</rights>"""
    source = """<rights>
<restrictionTemplate>
<name>ПоЗначениям</name>
<condition>ТИПОВОЕ СОДЕРЖИМОЕ</condition>
</restrictionTemplate>
</rights>"""
    monkeypatch.setattr(config.settings.review, "mode", ReviewMode.FULL_FILE_DIFF)
    monkeypatch.setattr(
        config.settings.review,
        "ignore_1c_role_restriction_templates",
        ["ПоЗначениям"],
        raising=False,
    )
    monkeypatch.setattr(
        "ai_review.services.diff.service.read_snapshot",
        lambda *_args, **kwargs: source if kwargs.get("head_sha") else None,
        raising=False,
    )

    out = DiffService.render_file(
        raw_diff=raw_diff,
        file="Roles/Test/Rights.rights",
        base_sha="a" * 40,
        head_sha="b" * 40,
    )

    assert "ТИПОВОЕ СОДЕРЖИМОЕ" not in out.diff
    assert out.added_lines == {1, 6}


def test_render_file_does_not_read_snapshots_for_non_rights_file(
    monkeypatch: pytest.MonkeyPatch,
    fake_diff: Diff,
) -> None:
    monkeypatch.setattr(
        "ai_review.services.diff.service.DiffParser.parse", lambda _: fake_diff
    )
    monkeypatch.setattr(config.settings.review, "mode", ReviewMode.FULL_FILE_DIFF)
    monkeypatch.setattr(
        config.settings.review, "ignore_1c_role_restriction_templates", ["ПоЗначениям"]
    )
    monkeypatch.setattr(
        "ai_review.services.diff.service.read_snapshot",
        lambda *_args, **_kwargs: pytest.fail("snapshot should not be read"),
    )

    DiffService.render_file(
        raw_diff="fake",
        file="CommonModules/Test/Module.bsl",
        base_sha="a",
        head_sha="b",
    )
