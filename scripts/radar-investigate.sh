#!/usr/bin/env bash
# The Radar bridge: Radar's AI SRE investigates a resource read-only, and its verdict becomes a reviewed Git change
# (.claude/skills/radar-fix), never a live patch.
#   radar-investigate.sh <kind>/<name> [-n <namespace>] [--out FILE]
#   radar-investigate.sh --verify <pr-number> <kind>/<name> [-n <namespace>] [--out FILE]
#   radar-investigate.sh --show <verdict.json>
#   radar-investigate.sh --markdown <verdict.json>
# Leave out -n only for a cluster-scoped resource, such as node/<name>. Needs radar and jq, and gh for --verify.
# A verdict is untrusted text (cluster data retold by an AI agent): it is printed with control characters dropped,
# posted with HTML, links, mentions and issue references neutralized, and never executed.
set -euo pipefail

DEFAULT_RADAR_URL="http://localhost:9281"  # the Radar instance that watches AKS
TARGET_RE='^[A-Za-z][A-Za-z0-9]*/[a-z0-9]([-a-z0-9.]*[a-z0-9])?$'
NAMESPACE_RE='^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'
URL_RE='^https?://[A-Za-z0-9.:/?=&%_~+-]+$'
DEPLOY_WORKFLOW="deploy.yml"  # deploys main to AKS on every merge

TMP_RAW=""
TMP_BODY=""
PHASE=""        # set by verify_phase: how the PR comment titles the check
DEPLOY_NOTE=""  # set by verify_phase: the evidence for that title

# jq definitions shared by the summary, the PR comment and the PR-body Markdown.
read -r -d '' JQ_LIB <<'JQ' || true
# Keep tabs and newlines; drop other control characters (C0, DEL, C1), so a verdict can't drive the terminal.
def clean: tostring | explode
  | map(select(. == 9 or . == 10 or (. >= 32 and . != 127 and (. < 128 or . > 159)))) | implode;
def text: if . == null then "" else clean end;
def clip($n): if length > $n then .[:$n] + "…" else . end;
def oneline: gsub("\\s+"; " ") | ltrimstr(" ") | rtrimstr(" ");
def arr: if type == "array" then . else [] end;
# Markdown for untrusted text: no HTML, no links or images, no mentions, no issue references. A verdict posted from the
# builder's account must not ping anyone, reference or close an issue, or give Gitar a command ("@gitar-bot ..."
# anywhere, or "gitar ..." at the start of a line). GitHub autolinks bare URLs and www. names, so they are defanged,
# and the brackets the defang adds are escaped too: an unescaped "www[.](//host)" is a link. A zero-width space after
# "#" and "GH-" stops "#12", "owner/repo#12" and "GH-12" from linking; a backslash doesn't.
def md: gsub("\\\\"; "\\\\") | gsub("&"; "&amp;") | gsub("<"; "&lt;") | gsub(">"; "&gt;")
  | gsub("\\["; "\\[") | gsub("\\]"; "\\]") | gsub("@"; "＠")
  | gsub("(?<s>[A-Za-z][A-Za-z0-9+.-]*)://"; "\(.s)\\[:\\]//") | gsub("(?<w>[Ww][Ww][Ww])\\."; "\(.w)\\[.\\]")
  | gsub("#"; "#\u200b") | gsub("(?<g>[Gg][Hh])-(?=[0-9])"; "\(.g)-\u200b");
def quote: split("\n") | map("> " + .) | join("\n");
def state:
  if .diagnosis.inconclusive == true then "inconclusive"
  elif .diagnosis.healthy == true then "healthy"
  elif (.diagnosis.rootCause | text) != "" then "root cause"
  else "no conclusion" end;
def facts:
  [ (.diagnosis.certainty | text | oneline | clip(40) | select(. != "") | "certainty \(.)"),
    (.diagnosis.confidence | select(type == "number") | "confidence \(.)") ] | join(", ");
def run_id:
  (.run | if type == "object" then (.id // .runId) else . end | text) as $id
  | if ($id | test("^[A-Za-z0-9._-]{1,100}$")) then $id else "" end;
# radar_url may be the investigation's own link or only the server's; add the run when it is only the server.
def run_link($server):
  run_id as $id
  | (.radar_url | text) as $u
  | if ($u | test("^https?://[A-Za-z0-9.:/?=&%_~+-]+$")) then
      (if ($u | test("^https?://[^/?#]+/?$")) and $id != "" then ($u | rtrimstr("/")) + "/?ai-run=" + $id else $u end)
    elif $id != "" then ($server | rtrimstr("/")) + "/?ai-run=" + $id
    else "" end;
# Radar's recommendation, read the way Radar's own UI reads it: recommendedIndex is 1-based into remediation. When
# steps and remediation have the same length they are parallel lists; then Radar recommends only a mitigate step, and
# that step's precondition is part of the recommendation.
def parallel: (.diagnosis.steps | arr | length) as $n | $n > 0 and $n == (.diagnosis.remediation | arr | length);
def rec_index:
  .diagnosis.recommendedIndex as $i
  | if ($i | type) == "number" and ($i | floor) == $i and $i >= 1 and $i <= (.diagnosis.remediation | arr | length)
       and ((parallel | not) or ((.diagnosis.steps | arr)[$i - 1] | if type == "object" then .kind else null end) == "mitigate")
    then $i else null end;
# The step to act on: Radar's recommended remediation, else its first mitigate step, else its first step, else its
# first remediation. A healthy verdict usually has none: its advice is only in the report.
def recommended:
  (.diagnosis.remediation | arr) as $r
  | (.diagnosis.steps | arr) as $all
  | ($all | map(select(type == "object"))) as $s
  | rec_index as $i
  | if $i != null then
      {text: ($r[$i - 1] | text), note: "remediation \($i) of \($r | length), recommended by Radar",
       pre: (if parallel then ($all[$i - 1].precondition | text) else "" end)}
    elif any($s[]; .kind == "mitigate") then
      first($s[] | select(.kind == "mitigate"))
      | {text: (.text | text), note: "first mitigate step", pre: (.precondition | text)}
    elif ($s | length) > 0 then
      $s[0] | {text: (.text | text), note: "first step, \(.kind | text | oneline | clip(20))", pre: (.precondition | text)}
    elif ($r | length) > 0 then {text: ($r[0] | text), note: "remediation 1 of \($r | length)", pre: ""}
    else null end;
def section($title; $value):
  ($value | text | clip(1500)) as $v
  | if $v == "" then empty else "", "**\($title)**", "", ($v | md | quote) end;
def list($title; $items):
  if ($items | length) == 0 then empty else "", "**\($title)**", "", ($items[] | "- " + md) end;
def rec_md:
  if . == null then empty
  else "", "**Recommended step** (\(.note | md))", "", ((.text | clip(1500)) + (if .pre != "" then "\nPrecondition: \(.pre | clip(300))" else "" end) | md | quote)
  end;
# Where a resource is: in a namespace, or cluster-scoped when there is none.
def where($ns): if $ns == "" then "(cluster-scoped)" else "in \($ns)" end;
# The terminal summary, as lines. $mode is investigate, verify or show; show prints the text in full.
def summary($target; $ns; $file; $server; $mode):
  def cap($n): if $mode == "show" then . else clip($n) end;
  def indent: gsub("\n"; "\n                  ");
  def row($label; $value): if $value == "" then empty else $label + ($value | indent) end;
  state as $state
  | recommended as $rec
  | ([run_id, run_link($server)] | map(select(. != "")) | join("  ")) as $run
  | [ "Radar verdict for \($target) \(where($ns)): \($state | ascii_upcase)" + (facts | if . == "" then "" else " (\(.))" end),
      row("Summary:          "; .diagnosis.summary | text | cap(1000)),
      row("Root cause:       "; .diagnosis.rootCause | text | cap(1000)),
      row("Recommended step: ";
        if $rec == null then
          "none in the structured verdict; any advice is in Radar's report"
          + (if $mode == "show" then ", below" else ": scripts/radar-investigate.sh --show \($file | @sh)" end)
        else
          ($rec.text | cap(1000)) + " (\($rec.note))" + (if $rec.pre != "" then "\nPrecondition: \($rec.pre | cap(300))" else "" end)
        end),
      row("Run:              "; if $run == "" then "not in the verdict" else $run end),
      row(if $mode == "show" then "Verdict file:     " else "Verdict saved:    " end; $file),
      (if $mode == "investigate" and $state == "root cause" then
        "", "Next: make the fix a reviewed Git change with the radar-fix skill (.claude/skills/radar-fix/SKILL.md). Never kubectl or Radar's Apply."
      else empty end)
    ];
JQ

# The terminal summary after an investigation. Needs $target, $ns ("" for cluster scope), $file, $server and $mode.
read -r -d '' JQ_TEXT <<'JQ' || true
summary($target; $ns; $file; $server; $mode) | join("\n")
JQ

# A saved verdict in full, as plain text with control characters dropped. Needs $file and $server.
read -r -d '' JQ_SHOW <<'JQ' || true
def lines($title; $items): if ($items | length) == 0 then empty else "", $title, $items[] end;
def hang: gsub("\n"; "\n     ");
rec_index as $ri
| ([.kind, .name] | map(text | oneline) | map(select(. != "")) | join("/")) as $target
| summary(if $target == "" then "an unnamed resource" else $target end; .namespace | text | oneline; $file; $server; "show")
  + [ lines("Steps:"; .diagnosis.steps | arr | map(select(type == "object")) | to_entries
        | map("  \(.key + 1). \(.value.kind | text | oneline): \(.value.text | text | hang)"
          + (if (.value.precondition | text) != "" then "\n     Precondition: \(.value.precondition | text | hang)" else "" end))),
      lines("Remediation:"; .diagnosis.remediation | arr | to_entries
        | map("  \(.key + 1). \(.value | text | hang)" + (if .key + 1 == $ri then " (recommended)" else "" end))),
      lines("Evidence:"; .diagnosis.evidence | arr | map(select(type == "object")
        | "  - \(.role | text | oneline) · \([.subject.kind, .subject.name] | map(text | oneline) | join("/")): \(.claim | text | hang)"
          + (if (.gap | text) != "" then "\n     Gap: \(.gap | text | hang)" else "" end))),
      lines("Ruled out:"; .diagnosis.ruledOut | arr | map(select(type == "object") | "  - \(.hypothesis | text | hang)")),
      (.diagnosis.report | text
        | if . == "" then empty else "", "Report:", (split("\n") | map(if . == "" then . else "  " + . end) | join("\n")) end) ]
| join("\n")
JQ

# The PR comment after a deploy. Needs $target, $ns ("" for cluster scope), $pr, $phase, $deploy and $server.
read -r -d '' JQ_COMMENT <<'JQ' || true
state as $state
| run_id as $id
| run_link($server) as $link
| [ "### Radar \($phase): \($state)",
    "",
    "Radar's read-only AI investigation (profile `safeguarded`) of `\($target)` \(if $ns == "" then "(cluster-scoped)" else "in namespace `\($ns)`" end).",
    "",
    "- **Verdict:** \($state)" + (facts | if . == "" then "" else " (\(md))" end),
    "- **Run:** \(if $id == "" then "not in the verdict" else "`\($id)`" end)\(if $link == "" then "" else " · \($link)" end)",
    "- **Deploy:** \($deploy)",
    section("Summary"; .diagnosis.summary),
    section("Root cause"; .diagnosis.rootCause),
    (recommended | rec_md),
    "",
    "Re-run: `scripts/radar-investigate.sh --verify \($pr) \($target)\(if $ns == "" then "" else " -n \($ns)" end)`. Radar's text is quoted as untrusted data, with HTML, links, mentions and issue references neutralized."
  ] | join("\n")
JQ

# A saved verdict as Markdown for a PR body. Needs $server.
read -r -d '' JQ_MARKDOWN <<'JQ' || true
def k8s_name: test("^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$");
state as $state
| run_id as $id
| run_link($server) as $link
| (rec_index // -1) as $ri
| (if (.kind | text | test("^[A-Za-z][A-Za-z0-9]*$")) and (.name | text | k8s_name)
   then "\(.kind | ascii_downcase)/\(.name)" else "<kind>/<name>" end) as $target
| (.namespace | text | oneline) as $where
| (if $where == "" then "" elif ($where | test("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")) then " -n \($where)"
   else " -n <namespace>" end) as $ns_flag
| [ "- **Verdict:** \($state)" + (facts | if . == "" then "" else " (\(md))" end),
    "- **Resource:** \([.kind, .name] | map(text | oneline) | join("/") | md)"
      + (if $where == "" then " (cluster-scoped)" else " in namespace \($where | md)" end),
    "- **Run:** \(if $id == "" then "not in the verdict" else "`\($id)`" end)\(if $link == "" then "" else " · \($link)" end)",
    "- **Verify after the deploy:** `scripts/radar-investigate.sh --verify <PR> \($target)\($ns_flag)`",
    section("Summary"; .diagnosis.summary),
    section("Root cause"; .diagnosis.rootCause),
    (recommended | rec_md),
    list("Steps"; .diagnosis.steps | arr | map(select(type == "object")
      | "\(.kind | text | oneline | clip(20)): \(.text | text | oneline | clip(500))"
        + (if (.precondition | text) != "" then " (precondition: \(.precondition | text | oneline | clip(300)))" else "" end))),
    list("Remediation"; .diagnosis.remediation | arr | to_entries
      | map("**\(.key + 1).** \(.value | text | oneline | clip(500))" + (if .key + 1 == $ri then " (recommended)" else "" end))),
    list("Evidence"; .diagnosis.evidence | arr | map(select(type == "object")
      | "\(.role | text | oneline | clip(20)) · \([.subject.kind, .subject.name] | map(text | oneline) | join("/")): \(.claim | text | oneline | clip(500))"
        + (if (.gap | text) != "" then " (gap: \(.gap | text | oneline | clip(300)))" else "" end))),
    list("Ruled out"; .diagnosis.ruledOut | arr | map(select(type == "object") | .hypothesis | text | oneline | clip(300))),
    (.diagnosis.report | text | clip(6000)
      | if . == "" then empty else "", "<details><summary>Radar's full report</summary>", "", (md | quote), "", "</details>" end)
  ] | join("\n")
JQ

usage() {
  cat <<'EOF'
Usage:
  radar-investigate.sh <kind>/<name> [-n <namespace>] [--out FILE]
      Run Radar's read-only AI investigation (profile safeguarded) of one resource, save the JSON verdict
      (default: ${TMPDIR:-/tmp}/radar-verdict-*.json, never inside the repository) and print a summary.
      Leave out -n only for a cluster-scoped resource, such as node/<name>.
  radar-investigate.sh --verify <pr-number> <kind>/<name> [-n <namespace>] [--out FILE]
      After the fix PR is deployed: investigate again and post the verdict summary, run id and link on the PR.
      The comment says "after deploy" only when the newest successful deploy.yml run on main includes the PR's
      merge commit; otherwise it says the deploy is not confirmed.
  radar-investigate.sh --show <verdict.json>
      Print a saved verdict in full (steps, remediation, evidence, report) with control characters dropped.
  radar-investigate.sh --markdown <verdict.json>
      Print a saved verdict as Markdown for a PR body, Radar's text quoted with HTML, links, mentions and issue
      references neutralized.

Environment:
  RADAR_URL    the running Radar instance (default http://localhost:9281, the AKS one)
  RADAR_AGENT  the agent Radar investigates with (default claude)

Examples:
  scripts/radar-investigate.sh deploy/conferenceops-discovery -n conferenceops
  scripts/radar-investigate.sh node/aks-nodepool1-12345678-vmss000000
EOF
}

err() { printf 'radar-investigate: %s\n' "$*" >&2; }

usage_error() { err "$1"; usage >&2; }

# shellcheck disable=SC2329  # run by the EXIT trap
cleanup() {
  [[ -z "$TMP_RAW" ]] || rm -f -- "$TMP_RAW"
  [[ -z "$TMP_BODY" ]] || rm -f -- "$TMP_BODY"
}

need() {  # need <command> <how to get it>
  command -v "$1" >/dev/null 2>&1 && return 0
  err "$1 is not installed or not on PATH. $2"
  return 1
}

where() {  # where <namespace or "">: the resource's scope, for messages
  if [[ -n "$1" ]]; then printf 'in %s\n' "$1"; else printf '(cluster-scoped)\n'; fi
}

repo_root() {  # the repository this script belongs to
  local src="${BASH_SOURCE[0]}" dir
  case "$src" in */*) dir="${src%/*}" ;; *) dir=. ;; esac
  (cd "$dir/.." && pwd -P)
}

resolve_out() {  # resolve_out <path or ""> <target> <namespace or "">: the verdict's absolute path, outside the repo
  local out="$1" target="$2" ns="$3" dir base abs root
  if [[ -z "$out" ]]; then
    dir="${TMPDIR:-/tmp}"
    base="radar-verdict-${ns:-cluster}-${target%%/*}-${target#*/}-$(date -u +%Y%m%dT%H%M%SZ)-$$.json"
  else
    case "$out" in
      */) err "--out must name a file, not a directory: $out"; return 2 ;;
      */*) dir="${out%/*}"; base="${out##*/}"; [[ -n "$dir" ]] || dir=/ ;;
      *) dir=.; base="$out" ;;
    esac
  fi
  abs=$(cd "$dir" 2>/dev/null && pwd -P) || { err "the directory for the verdict does not exist: $dir"; return 1; }
  root=$(repo_root) || return 1
  if [[ "$abs/" == "$root/"* ]]; then
    err "refusing to save the verdict inside the repository ($abs): it is cluster data. Use \${TMPDIR:-/tmp}."
    return 2
  fi
  if [[ -d "$abs/$base" ]]; then err "--out names a directory: $abs/$base"; return 2; fi
  printf '%s/%s\n' "${abs%/}" "$base"
}

is_verdict() {  # is_verdict <file>: exactly one JSON object with a diagnosis object
  jq -e -s 'length == 1 and (.[0] | type == "object" and (.diagnosis | type) == "object")' "$1" >/dev/null 2>&1
}

investigate() {  # investigate <target> <namespace or ""> <verdict path>
  local target="$1" ns="$2" out="$3" raw rc=0 args
  args=(diagnose "$target")
  if [[ -n "$ns" ]]; then args+=(-n "$ns"); fi
  args+=(--agent "$RADAR_AGENT" --profile safeguarded --json --yes --server "$RADAR_URL")
  raw=$(mktemp "$out.XXXXXX") || return 1
  TMP_RAW="$raw"
  echo "Radar is investigating $target $(where "$ns") at $RADAR_URL (read-only, profile safeguarded)..." >&2
  radar "${args[@]}" </dev/null >"$raw" || rc=$?
  if ((rc)); then
    err "radar diagnose failed (exit $rc). Is Radar running at $RADAR_URL? Start the AKS instance (port 9281) or set RADAR_URL."
    return 1
  fi
  if ! is_verdict "$raw"; then
    TMP_RAW=""  # keep Radar's output for inspection
    err "Radar's output is not a JSON verdict (one object with a diagnosis). Its raw output is in $raw"
    return 1
  fi
  mv -f -- "$raw" "$out" || return 1
  TMP_RAW=""
}

deploy_not_confirmed() {  # deploy_not_confirmed <pr> <why>
  err "warning: the deploy of PR #$1 is not confirmed: $2. The comment says so."
  PHASE="after merge, deploy not confirmed"
  DEPLOY_NOTE="not confirmed: $2"
}

# How the comment titles the check, from evidence rather than the PR's state alone: "after deploy" only when the PR
# merged into main and the newest successful deploy.yml run on main includes its merge commit. Sets PHASE and
# DEPLOY_NOTE; fails only when gh can't read the PR.
verify_phase() {  # verify_phase <pr>
  local pr="$1" view state="" base="" merge="" run="" run_id="" run_sha="" run_url="" status="" why=""
  view=$(gh pr view "$pr" --json state,baseRefName,mergeCommit \
    --jq '"\(.state) \(.baseRefName) \(.mergeCommit.oid // "-")"') || {
    err "cannot read PR #$pr with gh (signed in, and inside the repository?)"
    return 1
  }
  read -r state base merge <<<"$view" || true
  [[ "$state" =~ ^[A-Z_]+$ ]] || state=UNKNOWN
  if [[ "$state" != MERGED ]]; then
    err "warning: PR #$pr is $state, not merged, so its change is not deployed. The comment says so."
    PHASE="before deploy (PR #$pr is $state, not merged)"
    DEPLOY_NOTE="not deployed: PR #$pr is $state"
    return 0
  fi
  [[ "$base" =~ ^[A-Za-z0-9._/-]+$ ]] || base="another branch"
  if [[ "$base" != main ]]; then
    deploy_not_confirmed "$pr" "it merged into $base, and $DEPLOY_WORKFLOW deploys only main"
    return 0
  fi
  if [[ ! "$merge" =~ ^[0-9a-f]{40}$ ]]; then
    deploy_not_confirmed "$pr" "GitHub returned no merge commit for it"
    return 0
  fi
  if ! run=$(gh run list --workflow "$DEPLOY_WORKFLOW" --branch main --status success --limit 1 \
    --json databaseId,headSha,url --jq '.[] | "\(.databaseId) \(.headSha) \(.url)"'); then
    deploy_not_confirmed "$pr" "gh could not list the $DEPLOY_WORKFLOW runs on main"
    return 0
  fi
  read -r run_id run_sha run_url <<<"$run" || true
  if [[ ! "$run_id" =~ ^[0-9]+$ || ! "$run_sha" =~ ^[0-9a-f]{40}$ ]]; then
    deploy_not_confirmed "$pr" "no successful $DEPLOY_WORKFLOW run on main"
    return 0
  fi
  # identical or ahead: the deployed commit is the merge commit or a descendant of it.
  if ! status=$(gh api "repos/{owner}/{repo}/compare/$merge...$run_sha" --jq .status); then
    deploy_not_confirmed "$pr" "gh could not compare the merge commit ${merge:0:7} with the deployed ${run_sha:0:7}"
    return 0
  fi
  case "$status" in
    identical | ahead)
      [[ "$run_url" =~ ^https://[A-Za-z0-9./_-]+$ ]] || run_url=""
      PHASE="after deploy"
      DEPLOY_NOTE="$DEPLOY_WORKFLOW run $run_id succeeded at ${run_sha:0:7}, which includes the merge commit ${merge:0:7}"
      DEPLOY_NOTE+="${run_url:+ · $run_url}"
      ;;
    *)
      why="the newest successful $DEPLOY_WORKFLOW run on main ($run_id, at ${run_sha:0:7})"
      deploy_not_confirmed "$pr" "$why does not include the merge commit ${merge:0:7}"
      ;;
  esac
}

post_verdict() {  # post_verdict <pr> <verdict> <target> <namespace or ""> <phase> <deploy note>
  local body
  body=$(mktemp "${TMPDIR:-/tmp}/radar-comment.XXXXXX") || return 1
  TMP_BODY="$body"
  jq -r --arg pr "$1" --arg target "$3" --arg ns "$4" --arg phase "$5" --arg deploy "$6" --arg server "$RADAR_URL" \
    "$JQ_LIB $JQ_COMMENT" "$2" >"$body" || return 1
  gh pr comment "$1" --body-file "$body" || { err "gh pr comment failed; the verdict is still in $2"; return 1; }
}

main() {
  local mode=investigate pr="" target="" ns="" out="" file=""
  while (($#)); do
    case "$1" in
      -h | --help) usage; return 0 ;;
      --verify | --show | --markdown)
        [[ "$mode" == investigate ]] || { usage_error "use one of --verify, --show and --markdown"; return 2; }
        [[ $# -ge 2 ]] || { usage_error "$1 needs an argument"; return 2; }
        mode="${1#--}"
        if [[ "$mode" == verify ]]; then pr="$2"; else file="$2"; fi
        shift 2 ;;
      -n | --namespace)
        [[ $# -ge 2 && -n "$2" ]] || { usage_error "$1 needs a namespace"; return 2; }
        ns="$2"; shift 2 ;;
      --out)
        [[ $# -ge 2 ]] || { usage_error "--out needs a file"; return 2; }
        out="$2"; shift 2 ;;
      -*) usage_error "unknown option: $1"; return 2 ;;
      *)
        [[ -z "$target" ]] || { usage_error "unexpected argument: $1"; return 2; }
        target="$1"; shift ;;
    esac
  done

  RADAR_URL="${RADAR_URL:-$DEFAULT_RADAR_URL}"
  RADAR_AGENT="${RADAR_AGENT:-claude}"
  [[ "$RADAR_URL" =~ $URL_RE ]] || { err "RADAR_URL is not an http(s) URL: $RADAR_URL"; return 2; }

  if [[ "$mode" == show || "$mode" == markdown ]]; then
    [[ -z "$target$ns$out" ]] || { usage_error "--$mode takes only a verdict file"; return 2; }
    need jq "Install it: brew install jq." || return 1
    [[ -f "$file" ]] || { err "no such verdict file: $file"; return 1; }
    is_verdict "$file" || { err "not a Radar JSON verdict (one object with a diagnosis): $file"; return 1; }
    if [[ "$mode" == show ]]; then
      jq -r --arg file "$file" --arg server "$RADAR_URL" "$JQ_LIB $JQ_SHOW" "$file"
    else
      jq -r --arg server "$RADAR_URL" "$JQ_LIB $JQ_MARKDOWN" "$file"
    fi
    return
  fi

  [[ -n "$target" ]] || { usage_error "missing <kind>/<name>"; return 2; }
  [[ "$target" =~ $TARGET_RE ]] || { usage_error "not a <kind>/<name>: $target"; return 2; }
  if [[ -n "$ns" && ! "$ns" =~ $NAMESPACE_RE ]]; then usage_error "not a namespace name: $ns"; return 2; fi
  if [[ "$mode" == verify && ! "$pr" =~ ^[1-9][0-9]*$ ]]; then usage_error "not a PR number: $pr"; return 2; fi
  [[ "$RADAR_AGENT" =~ ^[a-z][a-z0-9-]*$ ]] || { err "RADAR_AGENT is not an agent name: $RADAR_AGENT"; return 2; }

  need radar "Install Radar (brew install kubectl-radar; https://radarhq.io) and start it against the cluster." || return 1
  need jq "Install it: brew install jq." || return 1
  if [[ "$mode" == verify ]]; then need gh "Install the GitHub CLI (brew install gh) and sign in." || return 1; fi

  local verdict
  verdict=$(resolve_out "$out" "$target" "$ns") || return
  # Before the investigation, so the title says what was deployed while Radar looked.
  if [[ "$mode" == verify ]]; then verify_phase "$pr" || return 1; fi

  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  investigate "$target" "$ns" "$verdict" || return 1
  jq -r --arg target "$target" --arg ns "$ns" --arg file "$verdict" --arg server "$RADAR_URL" --arg mode "$mode" \
    "$JQ_LIB $JQ_TEXT" "$verdict" || return 1

  if [[ "$mode" == verify ]]; then
    post_verdict "$pr" "$verdict" "$target" "$ns" "$PHASE" "$DEPLOY_NOTE" || return 1
    if [[ "$(jq -r "$JQ_LIB state" "$verdict")" != healthy ]]; then
      err "Radar does not report healthy, so this does not close the loop. Read the verdict and fix again."
    elif [[ "$PHASE" != "after deploy" ]]; then
      err "Radar reports healthy, but the deploy of PR #$pr is not confirmed, so this does not close the loop yet." \
        "Wait for the $DEPLOY_WORKFLOW run to succeed, then verify again."
    fi
  fi
}

main "$@"; exit
