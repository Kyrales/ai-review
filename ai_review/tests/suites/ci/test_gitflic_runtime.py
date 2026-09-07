from pathlib import Path
import subprocess
import textwrap


ROOT = Path(__file__).resolve().parents[4]
RUNTIME = ROOT / "ci/gitflic/runtime.sh"


def bash_path(path: Path | str) -> str:
    path = Path(path).resolve()
    if not path.drive:
        return path.as_posix()
    return f"/mnt/{path.drive[0].lower()}{str(path)[2:].replace('\\', '/')}"


def run_runtime(script: str, *, input: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f"source '{bash_path(RUNTIME)}';\n" + textwrap.dedent(script)],
        text=True,
        capture_output=True,
        input=input,
        check=False,
    )


def run_shell_file(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "runtime-test.sh"
    path.write_bytes(textwrap.dedent(script).encode())
    path.chmod(0o700)
    return subprocess.run(["bash", bash_path(path)], text=True, capture_output=True)


def test_image_contains_self_contained_gitflic_prepare_runtime():
    script = (ROOT / "ci/gitflic/prepare.sh").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "gitflic.ru/project/rt-vt/sppr.git" in script
    assert "--depth" not in script
    assert "merge-base" in script
    assert "+refs/heads/" in RUNTIME.read_text(encoding="utf-8")
    assert "GIT_ASKPASS" in script
    assert "mktemp /tmp" not in script
    assert 'mktemp "$runtime/work/' in script
    assert 'exec {work_lock_fd}>"$runtime/work/$work_name/.lock"' in script
    assert "COPY ci /opt/ai-review-ci" in dockerfile


def test_sppr_runtime_config_uses_embedded_russian_prompts():
    config = (ROOT / "ci/sppr/.ai-review-ones.yaml").read_text(encoding="utf-8")
    prompt = (ROOT / "ci/sppr/prompts/inline.md").read_text(encoding="utf-8")

    assert "/opt/ai-review-ci/sppr/prompts/inline.md" in config
    assert "Проведи ревью" in prompt


def test_publish_workflow_keylessly_signs_published_digest():
    workflow = (ROOT / ".github/workflows/workflow-publish.yml").read_text(encoding="utf-8")

    assert "id-token: write" in workflow
    assert "sigstore/cosign-installer@" in workflow
    assert "cosign sign --yes" in workflow
    assert "ghcr.io/kyrales/ai-review@sha256:*" in workflow


def test_publish_smoke_uses_embedded_config_without_artifact_overrides():
    workflow = (ROOT / ".github/workflows/workflow-publish.yml").read_text(encoding="utf-8")

    assert "AI_REVIEW_CONFIG_FILE_YAML=/opt/ai-review-ci/sppr/.ai-review-ones.yaml" in workflow
    assert "ARTIFACTS__LLM_DIR" not in workflow


def test_runtime_refuses_untrusted_urls_before_tokens_are_used():
    result = run_runtime('validate_https_url "https://token@gitflic.ru/project/rt-vt/sppr.git" "https://gitflic.ru/project/rt-vt/sppr.git"')

    assert result.returncode != 0
    assert "untrusted HTTPS endpoint" in result.stderr


def test_canary_scan_never_places_secret_in_process_argv(tmp_path: Path):
    canary = "canary-not-in-argv"
    artifact = tmp_path / "artifact"
    artifact.write_text(canary, encoding="utf-8")

    result = run_runtime(
        f'if scan_credential_canary "$(cat)" "{bash_path(tmp_path)}"; then exit 1; fi',
        input=canary,
    )

    assert result.returncode == 0
    assert canary not in result.stderr


def test_fetch_uses_disable_redirects_config(tmp_path: Path):
    log = tmp_path / "git-arguments"
    fake_git = tmp_path / "git"
    fake_git.write_bytes(b'#!/bin/sh\nprintf "%s\\n" "$*" > "$GIT_ARGUMENT_LOG"\n')
    fake_git.chmod(0o700)
    result = run_runtime(
        f'PATH="{bash_path(tmp_path)}:$PATH" GIT_ARGUMENT_LOG="{bash_path(log)}" fetch_refs "{bash_path(tmp_path / "cache.git")}" "https://127.0.0.1:8443/repo.git" source target',
    )

    assert result.returncode == 0
    assert "-c http.followRedirects=false" in log.read_text(encoding="utf-8")


def test_fetch_does_not_follow_redirect_from_http_fixture(tmp_path: Path):
    result = run_shell_file(tmp_path, f"""
        set -e
        source '{bash_path(RUNTIME)}'
        requests='{bash_path(tmp_path / "requests")}'
        git init --quiet --bare '{bash_path(tmp_path / "cache.git")}'
        python3 - "$requests" <<'PY' &
import http.server
import sys

class Redirect(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        with open(sys.argv[1], "a", encoding="utf-8") as output:
            output.write(self.path + "\\n")
        self.send_response(302)
        self.send_header("Location", "/redirected")
        self.end_headers()
    def log_message(self, *_):
        pass

http.server.HTTPServer(("127.0.0.1", 18991), Redirect).serve_forever()
PY
        server=$!
        sleep 1
        if fetch_refs '{bash_path(tmp_path / "cache.git")}' 'http://127.0.0.1:18991/repo.git' source target; then
            kill "$server"; exit 1
        fi
        kill "$server"; wait "$server" || true
        test "$(wc -l < "$requests")" -eq 1
        grep -Fx '/repo.git/info/refs?service=git-upload-pack' "$requests"
    """)

    assert result.returncode == 0, result.stderr


def test_cleanup_on_signal_preserves_signal_exit_status(tmp_path: Path):
    script = f"""
        source '{bash_path(RUNTIME)}'
        askpass="{bash_path(tmp_path)}/askpass"; fetch_log="{bash_path(tmp_path)}/fetch.log"
        : > "$askpass"; : > "$fetch_log"
        install_cleanup_traps
        kill -TERM $$
    """
    result = subprocess.run(["bash", "-c", textwrap.dedent(script)], text=True, capture_output=True)

    assert result.returncode == 143
    assert not (tmp_path / "askpass").exists()
    assert not (tmp_path / "fetch.log").exists()


def test_ttl_cleanup_skips_active_workdir_under_nonblocking_lock(tmp_path: Path):
    result = run_shell_file(tmp_path, f"""
        set -e
        source '{bash_path(RUNTIME)}'
        work='{bash_path(tmp_path / "work")}'
        mkdir -p "$work/mr-active" "$work/mr-stale"
        touch -d '2 minutes ago' "$work/mr-active" "$work/mr-stale"
        exec 9>"$work/mr-active/.lock"; flock -n 9
        cleanup_expired_workdirs "$work" 60
        test -d "$work/mr-active"
        test ! -e "$work/mr-stale"
    """)

    assert result.returncode == 0, result.stderr


def test_cache_reuses_warm_remote_and_recovers_from_corruption(tmp_path: Path):
    result = run_shell_file(tmp_path, f"""
        set -e
        source '{bash_path(RUNTIME)}'
        remote='{bash_path(tmp_path / "remote.git")}'
        seed='{bash_path(tmp_path / "seed")}'
        cache='{bash_path(tmp_path / "cache.git")}'
        git init --quiet --bare "$remote"
        git init --quiet "$seed"
        git -C "$seed" config user.email test@example.invalid
        git -C "$seed" config user.name test
        echo base > "$seed/file"; git -C "$seed" add file; git -C "$seed" commit --quiet -m base
        git -C "$seed" branch feature
        git -C "$seed" checkout --quiet feature; echo source >> "$seed/file"; git -C "$seed" commit --quiet -am source
        git -C "$seed" checkout --quiet master; echo target >> "$seed/file"; git -C "$seed" commit --quiet -am target
        git -C "$seed" push --quiet "$remote" master feature
        ensure_cache "$cache"; [[ "$cache_state" == miss ]]
        fetch_refs "$cache" "$remote" feature master
        git --git-dir="$cache" merge-base refs/ai-review/target refs/ai-review/source >/dev/null
        ensure_cache "$cache"; [[ "$cache_state" == hit ]]
        rm -rf "$cache"; mkdir "$cache"; touch "$cache/corrupt"
        ensure_cache "$cache"; [[ "$cache_state" == rebuilt ]]
    """)

    assert result.returncode == 0, result.stderr


def test_cache_reports_no_merge_base_and_flock_serializes_access(tmp_path: Path):
    result = run_shell_file(tmp_path, f"""
        set -e
        source '{bash_path(RUNTIME)}'
        remote='{bash_path(tmp_path / "remote.git")}'
        seed='{bash_path(tmp_path / "seed")}'
        cache='{bash_path(tmp_path / "cache.git")}'
        lock='{bash_path(tmp_path / "cache.lock")}'
        git init --quiet --bare "$remote"; git init --quiet "$seed"
        git -C "$seed" config user.email test@example.invalid; git -C "$seed" config user.name test
        echo one > "$seed/file"; git -C "$seed" add file; git -C "$seed" commit --quiet -m one
        git -C "$seed" branch source; git -C "$seed" checkout --quiet --orphan target; git -C "$seed" rm --quiet -rf .
        echo two > "$seed/file"; git -C "$seed" add file; git -C "$seed" commit --quiet -m two
        git -C "$seed" push --quiet "$remote" source target
        ensure_cache "$cache"; fetch_refs "$cache" "$remote" source target
        if git --git-dir="$cache" merge-base refs/ai-review/target refs/ai-review/source; then exit 1; fi
        exec 9>"$lock"; flock -n 9
        (exec 8>"$lock"; ! flock -n 8)
    """)

    assert result.returncode == 0, result.stderr


def test_ci_contract_keeps_image_config_prompts_and_secrets_out_of_regular_jobs():
    config = (ROOT / "ci/sppr/.ai-review-ones.yaml").read_text(encoding="utf-8")

    assert "COPY ci /opt/ai-review-ci" in (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "/opt/ai-review-ci/sppr/prompts/" in config
    assert "${" not in config
