#!/bin/sh
# Windows gate of a pre-release (activitytrack/activitytrack-doc#1): the real Windows jobs of a
# pre-release tag run on GitHub Actions (free on the public backup), not on GitLab's paid runners.
# This script, run by the GitLab job windows-github, waits for the GitHub workflow run of the
# tagged commit, fails unless it succeeded, and downloads the Windows files from the GitHub release
# of the tag (public assets: no token needed), checking them against its SHA256SUMS.
#
# Only the public REST API, without a token: 60 requests/hour per IP (304 answers count too), so
# one request every GATE_POLL seconds (GitHub caches the answer for 60 s anyway), and a wait for
# the reset when the limit is reached. A masked variable GITHUB_TOKEN_RO (a fine-grained token
# with public read access only) raises the limit to 5000/hour when the owner sets one.
#
# Environment (defaults for a GitLab tag pipeline):
#   GATE_REPO     GitHub repository, owner/name (required)
#   GATE_SHA      commit of the tag ($CI_COMMIT_SHA): the run is matched by it, not by name only
#   GATE_REF      tag ($CI_COMMIT_TAG): the run's head_branch; on a branch, the branch name
#   GATE_RELEASE  GitHub release to download from (default: GATE_REF)
#   GATE_FILES    asset names to download, separated by spaces (empty: verdict only)
#   GATE_OUT      directory for the files (default: dist)
#   GATE_INFO     file the run's address and verdict are written to (default: windows-gate.txt)
#   GATE_APPEAR   seconds to wait for the run to appear: the push mirror delay (default: 900)
#   GATE_TIMEOUT  seconds to wait, in all, for the run to finish (default: 5400)
#   GATE_POLL     seconds between two requests (default: 75)
#   GATE_API      API root (default: https://api.github.com)
# Exit status: 0 green and files downloaded, 1 red, missing or timed out, 2 usage.
#
# Try it against a past run: GATE_REPO=zipclaude/activitytrack-cli GATE_SHA=<sha> GATE_REF=<tag> sh <this>
set -eu

: "${GATE_REPO:?GATE_REPO=owner/name is required}"
GATE_SHA=${GATE_SHA:-${CI_COMMIT_SHA:-}}
GATE_REF=${GATE_REF:-${CI_COMMIT_TAG:-}}
GATE_RELEASE=${GATE_RELEASE:-$GATE_REF}
GATE_FILES=${GATE_FILES:-}
GATE_OUT=${GATE_OUT:-dist}
GATE_INFO=${GATE_INFO:-windows-gate.txt}
GATE_APPEAR=${GATE_APPEAR:-900}
GATE_TIMEOUT=${GATE_TIMEOUT:-5400}
GATE_POLL=${GATE_POLL:-75}
GATE_API=${GATE_API:-https://api.github.com}
WORKFLOW=.github/workflows/ci.yml

if [ -z "$GATE_SHA" ] || [ -z "$GATE_REF" ]; then
  echo "GATE_SHA and GATE_REF (or CI_COMMIT_SHA and CI_COMMIT_TAG) are required" >&2
  exit 2
fi

ACTIONS="https://github.com/$GATE_REPO/actions/workflows/ci.yml"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

log() { echo "[windows-gate $(date -u +%H:%M:%S)] $*" >&2; }
fail() {
  echo "windows-gate: $*" >&2
  echo "If GitHub is down, a maintainer can release with the Wine build instead: set the project" >&2
  echo "variable WINDOWS_GATE=skip (Settings > CI/CD > Variables), run the tag pipeline again, then remove it." >&2
  exit 1
}

# GET an API path into $TMP/body (kept from the last good answer); prints the HTTP status.
# A rate-limited answer waits for the reset, within the deadline.
api() {
  path=$1
  set -- -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28"
  if [ -n "${GITHUB_TOKEN_RO:-}" ]; then set -- "$@" -H "Authorization: Bearer $GITHUB_TOKEN_RO"; fi
  code=$(curl -sS -o "$TMP/new" -D "$TMP/headers" -w '%{http_code}' "$@" "$GATE_API$path") || code=000
  case "$code" in
    200)
      mv "$TMP/new" "$TMP/body" ;;
    403|429)
      remaining=$(sed -n 's/^x-ratelimit-remaining: *\([0-9]*\).*/\1/ip' "$TMP/headers")
      reset=$(sed -n 's/^x-ratelimit-reset: *\([0-9]*\).*/\1/ip' "$TMP/headers")
      if [ "${remaining:-1}" = 0 ] && [ -n "$reset" ]; then
        log "rate limited until $(date -u -d "@$reset" +%H:%M:%S 2>/dev/null || echo "$reset") (a GITHUB_TOKEN_RO variable raises the limit)"
        sleep_until $((reset - $(date +%s) + 5))
      fi ;;
  esac
  echo "$code"
}

# sleep N seconds, but not past the deadline
sleep_until() {
  left=$((deadline - $(date +%s)))
  if [ "$1" -le 0 ]; then :; elif [ "$1" -lt "$left" ]; then sleep "$1"; elif [ "$left" -gt 0 ]; then sleep "$left"; fi
}

start=$(date +%s)
appear_by=$((start + GATE_APPEAR))
deadline=$((start + GATE_TIMEOUT))
url="/repos/$GATE_REPO/actions/runs?head_sha=$GATE_SHA&event=push&per_page=50"
log "waiting for the run of $WORKFLOW on $GATE_REF at $GATE_SHA in $GATE_REPO"
run=
while :; do
  code=$(api "$url")
  if [ "$code" = 200 ]; then
    # the newest run of the workflow for this commit and this tag (a moved tag has another commit;
    # a ghtest/* branch at the same commit has another head_branch)
    run=$(jq -r --arg ref "$GATE_REF" --arg wf "$WORKFLOW" \
      '[.workflow_runs[] | select(.head_branch == $ref and .path == $wf)] | sort_by(.created_at) | last
       | select(. != null) | [.status, (.conclusion // "none"), .html_url] | @tsv' \
      "$TMP/body")
  else
    log "GitHub answered $code: trying again"
  fi
  now=$(date +%s)
  if [ -n "$run" ]; then
    status=$(printf '%s\n' "$run" | cut -f1)
    conclusion=$(printf '%s\n' "$run" | cut -f2)
    html=$(printf '%s\n' "$run" | cut -f3)
    if [ "$status" = completed ]; then break; fi
    log "run $html: $status"
    if [ "$now" -ge "$deadline" ]; then
      fail "the run $html has not finished after ${GATE_TIMEOUT}s ($status): look at it there."
    fi
  elif [ "$now" -ge "$appear_by" ] || [ "$now" -ge "$deadline" ]; then
    fail "no run of $WORKFLOW for $GATE_REF at $GATE_SHA after ${GATE_APPEAR}s. Look at $ACTIONS:
is the tag on GitHub at this commit (the push mirror: Settings > Repository > Mirroring repositories)?
Does the commit's $WORKFLOW start on a pre-release tag (on: push: tags)?"
  else
    log "no run yet (the mirror pushes the tag a few seconds after GitLab)"
  fi
  sleep_until "$GATE_POLL"
done

log "run $html: $conclusion"
{
  echo "run=$html"
  echo "conclusion=$conclusion"
  echo "sha=$GATE_SHA"
} > "$GATE_INFO"
if [ "$conclusion" != success ]; then
  fail "the GitHub run of $GATE_REF finished with \"$conclusion\": see $html"
fi

[ -n "$GATE_FILES" ] || { log "green; no files to download"; exit 0; }

# the release is created by the run's last job: it exists when the run is green
code=$(api "/repos/$GATE_REPO/releases/tags/$GATE_RELEASE")
[ "$code" = 200 ] || fail "no GitHub release $GATE_RELEASE (HTTP $code): https://github.com/$GATE_REPO/releases"
mkdir -p "$GATE_OUT"
asset() { jq -r --arg n "$1" '.assets[] | select(.name == $n) | .browser_download_url' "$TMP/body"; }
for f in $GATE_FILES; do
  u=$(asset "$f")
  [ -n "$u" ] || fail "the release $GATE_RELEASE has no $f; it has: $(jq -r '[.assets[].name] | join(" ")' "$TMP/body")"
  log "downloading $f"
  curl -fsSL --retry 3 -o "$GATE_OUT/$f" "$u" || fail "cannot download $u"
done
u=$(asset SHA256SUMS)
if [ -n "$u" ]; then
  curl -fsSL --retry 3 -o "$TMP/SHA256SUMS" "$u" || fail "cannot download $u"
  : > "$TMP/check"
  for f in $GATE_FILES; do
    awk -v n="$f" '$2 == n || $2 == "*" n' "$TMP/SHA256SUMS" > "$TMP/line"
    [ -s "$TMP/line" ] || fail "SHA256SUMS of $GATE_RELEASE does not list $f"
    cat "$TMP/line" >> "$TMP/check"
  done
  (cd "$GATE_OUT" && sha256sum -c "$TMP/check") || fail "the files do not match the SHA256SUMS of the release $GATE_RELEASE"
else
  log "the release has no SHA256SUMS: files not verified"
fi
ls -l "$GATE_OUT"
log "green; files downloaded from https://github.com/$GATE_REPO/releases/tag/$GATE_RELEASE"
