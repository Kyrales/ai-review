#!/usr/bin/env bash

validate_https_url() {
  local url=$1 expected=$2
  python3 - "$url" "$expected" <<'PY'
import sys
from urllib.parse import urlsplit

actual, expected = map(urlsplit, sys.argv[1:])
if (actual.scheme != "https" or actual.username or actual.password or actual.query
        or actual.fragment or actual.hostname != expected.hostname
        or actual.port is not None or actual.path != expected.path):
    print("untrusted HTTPS endpoint", file=sys.stderr)
    raise SystemExit(2)
PY
}

cleanup() {
  [[ -n "${askpass:-}" ]] && rm -f -- "$askpass"
  [[ -n "${fetch_log:-}" ]] && rm -f -- "$fetch_log"
  [[ -n "${canary_file:-}" ]] && rm -f -- "$canary_file"
  return 0
}

install_cleanup_traps() {
  trap cleanup EXIT
  trap 'cleanup; exit 129' HUP
  trap 'cleanup; exit 130' INT
  trap 'cleanup; exit 143' TERM
}

cleanup_expired_workdirs() {
  local work=$1 ttl=$2 candidate
  while IFS= read -r -d '' candidate; do
    (
      flock -n 9 || exit 0
      rm -rf -- "$candidate"
    ) 9>"$candidate/.lock"
  done < <(find "$work" -mindepth 1 -maxdepth 1 -type d -name 'mr-*' -mmin "+$(((ttl + 59) / 60))" -print0)
}

ensure_cache() {
  local cache=$1 replacement stale
  if git --git-dir="$cache" rev-parse --is-bare-repository >/dev/null 2>&1 &&
     git --git-dir="$cache" fsck --connectivity-only --no-dangling >/dev/null 2>&1; then
    cache_state=hit
    return
  fi
  cache_state=miss
  [[ -e "$cache" ]] && cache_state=rebuilt
  replacement=$(mktemp -d "${cache}.new-XXXXXXXX")
  git init --quiet --bare "$replacement"
  if [[ -e "$cache" ]]; then
    stale="${cache}.stale-$(date +%s)"
    mv -- "$cache" "$stale"
  fi
  mv -- "$replacement" "$cache"
}

fetch_refs() {
  local cache=$1 repo_url=$2 source_branch=$3 target_branch=$4
  git -c http.followRedirects=false --git-dir="$cache" fetch --quiet --force --no-tags "$repo_url" \
    "+refs/heads/$source_branch:refs/ai-review/source" \
    "+refs/heads/$target_branch:refs/ai-review/target"
}

scan_credential_canary() {
  local canary=$1 artifacts=$2 file
  canary_file=$(mktemp "${TMPDIR:-/tmp}/ai-review-canary-XXXXXXXX")
  chmod 600 "$canary_file"
  printf '%s\n' "$canary" >"$canary_file"
  if grep -Fq -f "$canary_file" "${fetch_log:-/dev/null}" ||
     git --git-dir="$cache" config --get-regexp '.*' 2>/dev/null | grep -Fq -f "$canary_file" ||
     grep -a -Fq -f "$canary_file" /proc/[0-9]*/cmdline 2>/dev/null; then
    echo 'credential canary detected' >&2
    return 5
  fi
  while IFS= read -r -d '' file; do
    if grep -Fq -f "$canary_file" "$file"; then
      rm -f -- "$file"
      echo 'credential canary detected' >&2
      return 5
    fi
  done < <(find "$artifacts" -type f -print0)
}
