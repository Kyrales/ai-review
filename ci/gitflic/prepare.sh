#!/usr/bin/env bash
set -Eeuo pipefail
set +x
umask 077

required=(AI_REVIEW_MR_ID AI_REVIEW_HEAD_SHA AI_REVIEW_SOURCE_BRANCH AI_REVIEW_TARGET_BRANCH AI_REVIEW_GITFLIC_TOKEN)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { echo "$name is required" >&2; exit 2; }
done
[[ "$AI_REVIEW_MR_ID" =~ ^[1-9][0-9]*$ ]] || { echo 'invalid MR id' >&2; exit 2; }
[[ "$AI_REVIEW_HEAD_SHA" =~ ^[0-9a-f]{40}$ ]] || { echo 'invalid head SHA' >&2; exit 2; }
branch_re='^[A-Za-z0-9._/-]+$'
[[ "$AI_REVIEW_SOURCE_BRANCH" =~ $branch_re && "$AI_REVIEW_SOURCE_BRANCH" != /* && "$AI_REVIEW_SOURCE_BRANCH" != *..* ]] || exit 2
[[ "$AI_REVIEW_TARGET_BRANCH" =~ $branch_re && "$AI_REVIEW_TARGET_BRANCH" != /* && "$AI_REVIEW_TARGET_BRANCH" != *..* ]] || exit 2

runtime=/runtime
artifacts=/artifacts
repo_url=https://gitflic.ru/project/rt-vt/sppr.git
mkdir -p "$runtime/cache" "$runtime/work" "$runtime/locks" "$artifacts/run"
chmod 700 "$runtime/cache" "$runtime/work" "$runtime/locks" "$artifacts/run"
[[ $(realpath "$runtime") == /runtime ]] || { echo 'invalid runtime mount' >&2; exit 2; }

mapfile -t mr_state < <(python3 - <<'PY'
import json, os, time, urllib.error, urllib.request

url = f"https://api.gitflic.ru/project/rt-vt/sppr/merge-request/{os.environ['AI_REVIEW_MR_ID']}"
request = urllib.request.Request(url, headers={"Authorization": "token " + os.environ["AI_REVIEW_GITFLIC_TOKEN"]})
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)

opener = urllib.request.build_opener(NoRedirect)
for attempt in range(3):
    try:
        with opener.open(request, timeout=30) as response:
            mr = json.load(response)
        break
    except urllib.error.HTTPError as error:
        if error.code not in {408, 429, 500, 502, 503, 504} or attempt == 2:
            raise
        delay = error.headers.get("Retry-After", "")
        time.sleep(float(delay) if delay.isdigit() else 0.5 * (2 ** attempt))
    except urllib.error.URLError:
        if attempt == 2:
            raise
        time.sleep(0.5 * (2 ** attempt))
actual = (mr["status"]["id"], mr["sourceBranch"]["id"], mr["sourceBranch"]["hash"], mr["targetBranch"]["id"])
expected = ("OPENED", os.environ["AI_REVIEW_SOURCE_BRANCH"], os.environ["AI_REVIEW_HEAD_SHA"], os.environ["AI_REVIEW_TARGET_BRANCH"])
if actual != expected:
    raise SystemExit("MR state changed before checkout")
print(mr["targetBranch"]["hash"])
PY
)
[[ ${#mr_state[@]} -eq 1 && ${mr_state[0]} =~ ^[0-9a-f]{40}$ ]] || exit 4
target_sha=${mr_state[0]}

cache="$runtime/cache/sppr.git"
exec 7>"$runtime/locks/cache.lock"
flock -w 300 7 || { echo 'cache lock timeout' >&2; exit 3; }
cache_state=hit
if ! git --git-dir="$cache" rev-parse --is-bare-repository >/dev/null 2>&1 ||
   ! git --git-dir="$cache" fsck --connectivity-only --no-dangling >/dev/null 2>&1; then
  [[ -e "$cache" ]] && cache_state=rebuilt || cache_state=miss
  replacement=$(mktemp -d "$runtime/cache/sppr.git.new-XXXXXXXX")
  git init --quiet --bare "$replacement"
  if [[ -e "$cache" ]]; then
    stale="$runtime/cache/sppr.git.stale-$(date +%s)"
    mv -- "$cache" "$stale"
  fi
  mv -- "$replacement" "$cache"
fi

askpass=$(mktemp "$runtime/work/ai-review-askpass-XXXXXXXX")
cleanup_auth() { rm -f -- "$askpass"; }
trap cleanup_auth EXIT HUP INT TERM
printf '%s\n' '#!/bin/sh' 'case "$1" in *Username*) printf "%s\\n" "${AI_REVIEW_GITFLIC_USERNAME:-karataev-oa}";; *) printf "%s\\n" "$AI_REVIEW_GITFLIC_TOKEN";; esac' >"$askpass"
chmod 700 "$askpass"
for attempt in 1 2 3; do
  if GIT_ASKPASS="$askpass" GIT_TERMINAL_PROMPT=0 git --git-dir="$cache" fetch --quiet --force --no-tags "$repo_url" \
    "+refs/heads/$AI_REVIEW_SOURCE_BRANCH:refs/ai-review/source" \
    "+refs/heads/$AI_REVIEW_TARGET_BRANCH:refs/ai-review/target"; then
    break
  fi
  [[ "$attempt" -lt 3 ]] || { echo 'repository fetch failed after retries' >&2; exit 3; }
  sleep $((2 ** (attempt - 1)))
done
cleanup_auth
trap - EXIT HUP INT TERM

source_sha=$(git --git-dir="$cache" rev-parse 'refs/ai-review/source^{commit}')
fetched_target_sha=$(git --git-dir="$cache" rev-parse 'refs/ai-review/target^{commit}')
[[ "$source_sha" == "$AI_REVIEW_HEAD_SHA" ]] || { echo 'source branch changed during fetch' >&2; exit 4; }
[[ "$fetched_target_sha" == "$target_sha" ]] || { echo 'target branch changed during fetch' >&2; exit 4; }
base_sha=$(git --git-dir="$cache" merge-base refs/ai-review/target refs/ai-review/source)
[[ "$base_sha" =~ ^[0-9a-f]{40}$ ]] || { echo 'merge base not found' >&2; exit 4; }

work_name=$(basename "$(mktemp -d "$runtime/work/mr-${AI_REVIEW_MR_ID}-XXXXXXXX")")
source_dir="$runtime/work/$work_name/source"
git init --quiet "$source_dir"
git -C "$source_dir" fetch --quiet --no-tags "$cache" \
  '+refs/ai-review/source:refs/remotes/origin/source' \
  '+refs/ai-review/target:refs/remotes/origin/target'
git -C "$source_dir" checkout --quiet --detach "$source_sha"
mkdir -p "$source_dir/artifacts"

cat >"$artifacts/run/prepared.env" <<EOF
AI_REVIEW_WORK_NAME=$work_name
AI_REVIEW_BASE_SHA=$base_sha
AI_REVIEW_TARGET_SHA=$target_sha
AI_REVIEW_CACHE_STATE=$cache_state
EOF
