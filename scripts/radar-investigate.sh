#!/usr/bin/env bash
# The Radar bridge: Radar's AI SRE investigates a workload read-only, and its verdict becomes a reviewed Git change
# (.claude/skills/radar-fix), never a live patch.
#   radar-investigate.sh <kind>/<name> -n <namespace> [--out FILE]
#   radar-investigate.sh --verify <pr-number> <kind>/<name> -n <namespace> [--out FILE]
#   radar-investigate.sh --markdown <verdict.json>
# Needs radar and jq, and gh for --verify. A verdict is untrusted text (cluster data retold by an AI agent): it is
# printed and posted with control characters, HTML, links and mentions neutralized, and never executed.
set -euo pipefail

DEFAULT_RADAR_URL="http://localhost:9281"  # the Radar instance that watches AKS
TARGET_RE='^[A-Za-z][A-Za-z0-9]*/[a-z0-9]([-a-z0-9.]*[a-z0-9])?$'
NAMESPACE_RE='^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'
URL_RE='^https?://[A-Za-z0-9.:/?=&%_~+-]+$'

TMP_RAW=""
TMP_BODY=""

# jq definitions shared by the summary, the PR comment and the PR-body Markdown.
read -r -d '' JQ_LIB <<'JQ' || true
# Keep tabs and newlines; drop other control characters (C0, DEL, C1), so a verdict can't drive the terminal.
def clean: tostring | explode
  | map(select(. == 9 or . == 10 or (. >= 32 and . != 127 and (. < 128 or . > 159)))) | implode;
def text: if . == null then "" else clean end;
def clip($n): if length > $n then .[:$n] + "…" else . end;
def oneline: gsub("\\s+"; " ") | ltrimstr(" ") | rtrimstr(" ");
def arr: if type == "array" then . else [] end;
# Markdown for untrusted text: no HTML, no links or images, no mentions. A verdict posted from the builder's account
# must not ping anyone or give Gitar a command ("@gitar-bot ..." anywhere, or "gitar ..." at the start of a line).
def md: gsub("\\\\"; "\\\\") | gsub("&"; "&amp;") | gsub("<"; "&lt;") | gsub(">"; "&gt;")
  | gsub("\\["; "\\[") | gsub("\\]"; "\\]") | gsub("@"; "＠")
  | gsub("(?<s>[A-Za-z][A-Za-z0-9+.-]*)://"; "\(.s)[:]//") | gsub("(?i)www\\."; "www[.]");
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
# The step to act on: Radar's recommended remediation, else its first mitigate step, else its first step, else its
# first remediation. A healthy verdict usually has none: its advice is only in the report.
def recommended:
  (.diagnosis.remediation | arr) as $r
  | (.diagnosis.steps | arr | map(select(type == "object"))) as $s
  | .diagnosis.recommendedIndex as $i
  | if ($i | type) == "number" and $i >= 1 and $i <= ($r | length) and ($i | floor) == $i then
      {text: ($r[$i - 1] | text), note: "remediation \($i) of \($r | length), recommended by Radar", pre: ""}
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
JQ

# The terminal summary. Needs $target, $ns, $file, $server and $mode.
read -r -d '' JQ_TEXT <<'JQ' || true
def indent: gsub("\n"; "\n                  ");
def row($label; $value): if $value == "" then empty else $label + ($value | indent) end;
state as $state
| recommended as $rec
| ([run_id, run_link($server)] | map(select(. != "")) | join("  ")) as $run
| [ "Radar verdict for \($target) in \($ns): \($state | ascii_upcase)" + (facts | if . == "" then "" else " (\(.))" end),
    row("Summary:          "; .diagnosis.summary | text | clip(1000)),
    row("Root cause:       "; .diagnosis.rootCause | text | clip(1000)),
    row("Recommended step: ";
      if $rec == null then
        "none in the structured verdict; any advice is in Radar's report: jq -r .diagnosis.report \($file | @sh)"
      else
        ($rec.text | clip(1000)) + " (\($rec.note))" + (if $rec.pre != "" then "\nPrecondition: \($rec.pre | clip(300))" else "" end)
      end),
    row("Run:              "; if $run == "" then "not in the verdict" else $run end),
    row("Verdict saved:    "; $file),
    (if $mode == "investigate" and $state == "root cause" then
      "", "Next: make the fix a reviewed Git change with the radar-fix skill (.claude/skills/radar-fix/SKILL.md). Never kubectl or Radar's Apply."
    else empty end)
  ] | join("\n")
JQ

# The PR comment after a deploy. Needs $target, $ns, $pr, $phase and $server.
read -r -d '' JQ_COMMENT <<'JQ' || true
state as $state
| run_id as $id
| run_link($server) as $link
| [ "### Radar \($phase): \($state)",
    "",
    "Radar's read-only AI investigation (profile `safeguarded`) of `\($target)` in namespace `\($ns)`.",
    "",
    "- **Verdict:** \($state)" + (facts | if . == "" then "" else " (\(md))" end),
    "- **Run:** \(if $id == "" then "not in the verdict" else "`\($id)`" end)\(if $link == "" then "" else " · \($link)" end)",
    section("Summary"; .diagnosis.summary),
    section("Root cause"; .diagnosis.rootCause),
    (recommended | rec_md),
    "",
    "Re-run: `scripts/radar-investigate.sh --verify \($pr) \($target) -n \($ns)`. Radar's text is quoted as untrusted data, with HTML, links and mentions neutralized."
  ] | join("\n")
JQ

# A saved verdict as Markdown for a PR body. Needs $server.
read -r -d '' JQ_MARKDOWN <<'JQ' || true
def k8s_name: test("^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$");
state as $state
| run_id as $id
| run_link($server) as $link
| (.diagnosis.recommendedIndex | if type == "number" then . else -1 end) as $ri
| (if (.kind | text | test("^[A-Za-z][A-Za-z0-9]*$")) and (.name | text | k8s_name)
   then "\(.kind | ascii_downcase)/\(.name)" else "<kind>/<name>" end) as $target
| (if (.namespace | text | test("^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")) then .namespace else "<namespace>" end) as $ns
| [ "- **Verdict:** \($state)" + (facts | if . == "" then "" else " (\(md))" end),
    "- **Resource:** \([.kind, .name] | map(text | oneline) | join("/") | md) in namespace \(.namespace | text | oneline | md)",
    "- **Run:** \(if $id == "" then "not in the verdict" else "`\($id)`" end)\(if $link == "" then "" else " · \($link)" end)",
    "- **Verify after the deploy:** `scripts/radar-investigate.sh --verify <PR> \($target) -n \($ns)`",
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
  radar-investigate.sh <kind>/<name> -n <namespace> [--out FILE]
      Run Radar's read-only AI investigation (profile safeguarded) of one resource, save the JSON verdict
      (default: ${TMPDIR:-/tmp}/radar-verdict-*.json, never inside the repository) and print a summary.
  radar-investigate.sh --verify <pr-number> <kind>/<name> -n <namespace> [--out FILE]
      After the fix PR is deployed: investigate again and post the verdict summary, run id and link on the PR.
  radar-investigate.sh --markdown <verdict.json>
      Print a saved verdict as Markdown for a PR body, Radar's text quoted with HTML, links and mentions neutralized.

Environment:
  RADAR_URL    the running Radar instance (default http://localhost:9281, the AKS one)
  RADAR_AGENT  the agent Radar investigates with (default claude)

Example:
  scripts/radar-investigate.sh deploy/conferenceops-discovery -n conferenceops
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

repo_root() {  # the repository this script belongs to
  local src="${BASH_SOURCE[0]}" dir
  case "$src" in */*) dir="${src%/*}" ;; *) dir=. ;; esac
  (cd "$dir/.." && pwd -P)
}

resolve_out() {  # resolve_out <path or ""> <target> <namespace>: the absolute verdict path, never in the repository
  local out="$1" target="$2" ns="$3" dir base abs root
  if [[ -z "$out" ]]; then
    dir="${TMPDIR:-/tmp}"
    base="radar-verdict-${ns}-${target%%/*}-${target#*/}-$(date -u +%Y%m%dT%H%M%SZ)-$$.json"
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

investigate() {  # investigate <target> <namespace> <verdict path>
  local target="$1" ns="$2" out="$3" raw rc=0
  raw=$(mktemp "$out.XXXXXX") || return 1
  TMP_RAW="$raw"
  echo "Radar is investigating $target in $ns at $RADAR_URL (read-only, profile safeguarded)..." >&2
  radar diagnose "$target" -n "$ns" --agent "$RADAR_AGENT" --profile safeguarded --json --yes \
    --server "$RADAR_URL" </dev/null >"$raw" || rc=$?
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

verify_phase() {  # verify_phase <pr>: how the comment titles the check; warns when the PR isn't merged
  local state
  state=$(gh pr view "$1" --json state --jq .state) || {
    err "cannot read PR #$1 with gh (signed in, and inside the repository?)"
    return 1
  }
  [[ "$state" =~ ^[A-Z_]+$ ]] || state=UNKNOWN
  if [[ "$state" == MERGED ]]; then
    echo "after deploy"
  else
    err "warning: PR #$1 is $state, not merged, so its change is not deployed. The comment says so."
    echo "before deploy (PR #$1 is $state, not merged)"
  fi
}

post_verdict() {  # post_verdict <pr> <verdict> <target> <namespace> <phase>
  local body
  body=$(mktemp "${TMPDIR:-/tmp}/radar-comment.XXXXXX") || return 1
  TMP_BODY="$body"
  jq -r --arg pr "$1" --arg target "$3" --arg ns "$4" --arg phase "$5" --arg server "$RADAR_URL" \
    "$JQ_LIB $JQ_COMMENT" "$2" >"$body" || return 1
  gh pr comment "$1" --body-file "$body" || { err "gh pr comment failed; the verdict is still in $2"; return 1; }
}

main() {
  local mode=investigate pr="" target="" ns="" out="" file=""
  while (($#)); do
    case "$1" in
      -h | --help) usage; return 0 ;;
      --verify | --markdown)
        [[ "$mode" == investigate ]] || { usage_error "use one of --verify and --markdown"; return 2; }
        [[ $# -ge 2 ]] || { usage_error "$1 needs an argument"; return 2; }
        if [[ "$1" == --verify ]]; then mode=verify; pr="$2"; else mode=markdown; file="$2"; fi
        shift 2 ;;
      -n | --namespace)
        [[ $# -ge 2 ]] || { usage_error "$1 needs a namespace"; return 2; }
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

  if [[ "$mode" == markdown ]]; then
    [[ -z "$target$ns$out" ]] || { usage_error "--markdown takes only a verdict file"; return 2; }
    need jq "Install it: brew install jq." || return 1
    [[ -f "$file" ]] || { err "no such verdict file: $file"; return 1; }
    is_verdict "$file" || { err "not a Radar JSON verdict (one object with a diagnosis): $file"; return 1; }
    jq -r --arg server "$RADAR_URL" "$JQ_LIB $JQ_MARKDOWN" "$file"
    return
  fi

  [[ -n "$target" ]] || { usage_error "missing <kind>/<name>"; return 2; }
  [[ "$target" =~ $TARGET_RE ]] || { usage_error "not a <kind>/<name>: $target"; return 2; }
  [[ -n "$ns" ]] || { usage_error "missing -n <namespace>"; return 2; }
  [[ "$ns" =~ $NAMESPACE_RE ]] || { usage_error "not a namespace name: $ns"; return 2; }
  if [[ "$mode" == verify && ! "$pr" =~ ^[1-9][0-9]*$ ]]; then usage_error "not a PR number: $pr"; return 2; fi
  [[ "$RADAR_AGENT" =~ ^[a-z][a-z0-9-]*$ ]] || { err "RADAR_AGENT is not an agent name: $RADAR_AGENT"; return 2; }

  need radar "Install Radar (brew install kubectl-radar; https://radarhq.io) and start it against the cluster." || return 1
  need jq "Install it: brew install jq." || return 1
  if [[ "$mode" == verify ]]; then need gh "Install the GitHub CLI (brew install gh) and sign in." || return 1; fi

  local verdict phase=""
  verdict=$(resolve_out "$out" "$target" "$ns") || return
  if [[ "$mode" == verify ]]; then phase=$(verify_phase "$pr") || return 1; fi

  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  investigate "$target" "$ns" "$verdict" || return 1
  jq -r --arg target "$target" --arg ns "$ns" --arg file "$verdict" --arg server "$RADAR_URL" --arg mode "$mode" \
    "$JQ_LIB $JQ_TEXT" "$verdict" || return 1

  if [[ "$mode" == verify ]]; then
    post_verdict "$pr" "$verdict" "$target" "$ns" "$phase" || return 1
    if [[ "$(jq -r "$JQ_LIB state" "$verdict")" != healthy ]]; then
      err "Radar does not report healthy, so this does not close the loop. Read the verdict and fix again."
    fi
  fi
}

main "$@"; exit
