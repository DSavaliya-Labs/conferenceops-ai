#!/usr/bin/env bash
# Add one entry per closed pull request to the "Build log" issue: merged or closed without merging, Gitar's verdict,
# and any CI failures on the way. The entry is built from GitHub data only, and re-running skips PRs already logged.
#   Usage: build-log.sh <pr-number>...      Needs GH_TOKEN and GH_REPO.
set -euo pipefail

LOG_TITLE="Build log"
VENUE_TZ="America/Denver"

log_issue() {
  local n
  n=$(gh issue list --state open --search "\"$LOG_TITLE\" in:title" --json number,title \
    --jq "[.[] | select(.title == \"$LOG_TITLE\")] | min_by(.number) | .number // empty") || return 1
  if [[ -z "$n" ]]; then
    local url
    url=$(gh issue create --title "$LOG_TITLE" --body "One entry per closed pull request: what landed, what Gitar \
said, and what it blocked. Written by \`scripts/build-log.sh\` from GitHub data.") || return 1
    n="${url##*/}"
  fi
  echo "$n"
}

already_logged() {  # already_logged <issue> <pr>
  local bodies
  bodies=$(gh api "repos/$GH_REPO/issues/$1/comments" --paginate --jq '.[].body') || exit 1
  grep -qF "<!-- build-log:pr=$2 -->" <<<"$bodies"
}

venue_time() {  # venue_time <iso-8601>
  TZ="$VENUE_TZ" date -d "$1" '+%a %b %-d, %-I:%M %p %Z'
}

kbds() {  # the text of each <kbd> on one line of HTML, joined with " · "
  grep -o '<kbd>[^<]*</kbd>' | sed -e 's/<[^>]*>//g' | paste -sd '|' - | sed 's/|/ · /g'
}

gitar_line() {  # gitar_line <pr>: verdict · risk · findings · rules · auto-approval, from Gitar's summary comment
  local body
  body=$(gh api "repos/$GH_REPO/issues/$1/comments" --paginate \
    --jq '[.[] | select(.user.login | startswith("gitar"))] | last | .body // ""') || return 1
  if [[ -z "$body" ]]; then echo "no Gitar summary"; return 0; fi
  local review risk rules approval parts=()
  review=$(grep -m1 '<b>Code Review</b>' <<<"$body" | kbds || true)
  risk=$(grep -o -m1 '<kbd>[^<]*[Rr]isk</kbd>' <<<"$body" | sed -e 's/<[^>]*>//g' || true)
  rules=$(grep -m1 '<b>Rules</b>' <<<"$body" | kbds | sed 's/ · [0-9]* not applicable//' || true)
  approval=$(grep -m1 '<b>Auto-approval</b>' <<<"$body" | kbds || true)
  [[ -n "$review" ]] && parts+=("$review")
  [[ -n "$risk" ]] && parts+=("$risk")
  [[ -n "$rules" ]] && parts+=("rules: $rules")
  [[ -n "$approval" ]] && parts+=("auto-approval: $approval")
  local IFS='|'
  local joined="${parts[*]}"
  echo "${joined//|/ · }"
}

ci_failures() {  # ci_failures <pr>: failed or timed-out check runs on any of the PR's commits, Gitar's own excluded
  local shas sha all=""
  shas=$(gh pr view "$1" --json commits --jq '.commits[].oid') || return 1
  for sha in $shas; do
    all+=$(gh api "repos/$GH_REPO/commits/$sha/check-runs?filter=all&per_page=100" \
      --jq '.check_runs[] | select(.app.slug != "gitar-bot")
            | select(.conclusion == "failure" or .conclusion == "timed_out") | .name') || return 1
    all+=$'\n'
  done
  grep -v '^$' <<<"$all" | sort | uniq -c \
    | awk '{n=$1; $1=""; sub(/^ /, ""); printf "%s%s ×%s", (NR>1 ? ", " : ""), $0, n}' || true
}

entry() {  # entry <pr>
  local pr="$1" meta title state merged_at merged_by closed_at sha outcome
  meta=$(gh pr view "$pr" --json title,state,mergedAt,mergedBy,closedAt,mergeCommit,author) || return 1
  title=$(jq -r '.title' <<<"$meta"); title="${title//</&lt;}"
  state=$(jq -r '.state' <<<"$meta")
  merged_at=$(jq -r '.mergedAt // empty' <<<"$meta")
  merged_by=$(jq -r '.mergedBy.login // "unknown"' <<<"$meta")
  closed_at=$(jq -r '.closedAt // empty' <<<"$meta")
  sha=$(jq -r '.mergeCommit.oid // empty' <<<"$meta")
  if [[ "$state" == "MERGED" ]]; then
    outcome="merged $(venue_time "$merged_at") by ${merged_by} · ${sha:0:7}"
  elif [[ "$state" == "CLOSED" ]]; then
    outcome="**closed without merging** $(venue_time "$closed_at")"
  else
    echo "PR #$pr is still open; nothing to log." >&2
    return 2
  fi
  local gitar failures ci
  gitar=$(gitar_line "$pr") || return 1
  failures=$(ci_failures "$pr") || return 1
  if [[ -n "$failures" ]]; then ci="failed on the way: $failures"; else ci="green first time"; fi
  printf '<!-- build-log:pr=%s -->\n**#%s %s** · %s\nGitar: %s\nCI: %s\n' "$pr" "$pr" "$title" "$outcome" "$gitar" "$ci"
}

main() {
  (($#)) || { echo "usage: $0 <pr-number>..." >&2; return 2; }
  local issue pr body
  issue=$(log_issue) || return 1
  for pr in "$@"; do
    [[ "$pr" =~ ^[0-9]+$ ]] || { echo "not a PR number: $pr" >&2; return 2; }
    if already_logged "$issue" "$pr"; then echo "#$pr is already in the Build log."; continue; fi
    body=$(entry "$pr") || { [[ $? -eq 2 ]] && continue; return 1; }
    gh issue comment "$issue" --body "$body" >/dev/null || return 1
    echo "Logged #$pr in issue #$issue."
  done
}

main "$@"; exit
