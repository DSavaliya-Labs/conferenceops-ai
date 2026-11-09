#!/usr/bin/env bash
# Apply everything in observability/dash0/ to the Dash0 dataset `conferenceops`, idempotently:
#   - `dash0 apply`: the dataset's processing settings, the dashboard and the check rules. Each carries a fixed id,
#     so a re-run updates them in place instead of creating copies.
#   - the automations API (`dash0 apply` has no automation kind): the Agent0 automation, upserted by its
#     `dash0.com/origin` label, with its prompt read from the .prompt.md file next to its definition.
# Then it reads every asset back from Dash0 and prints its name and id.
#   Usage: scripts/dash0-apply.sh [--dry-run]     --dry-run validates everything and changes nothing.
#   Needs DASH0_API_URL and DASH0_AUTH_TOKEN in the environment (. ~/.config/conferenceops/env.sh), the dash0 CLI
#   (1.20 or later) and jq. The CLI reads the token from the environment; this script never prints it.
set -euo pipefail

DATASET="conferenceops"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSETS="$ROOT/observability/dash0"
AUTOMATION="$ASSETS/automations/discovery-capacity-fix"  # .json (definition) and .prompt.md (prompt)
TRIGGER_RULE="$ASSETS/checks/discovery-rejecting-searches.yaml"

die() {
  echo "dash0-apply: $*" >&2
  exit 1
}

require() {
  local name
  for name in DASH0_API_URL DASH0_AUTH_TOKEN; do
    [[ -n "${!name:-}" ]] || die "$name is not set. Load it first, for example: . ~/.config/conferenceops/env.sh"
  done
  command -v dash0 >/dev/null || die "the dash0 CLI is not installed"
  command -v jq >/dev/null || die "jq is not installed"
}

api() {  # api <METHOD> <path> [-f <file|->]: call the Dash0 API for the dataset and print the JSON response
  dash0 -X api "$@" --dataset "$DATASET"
}

rule_id() {  # rule_id <CheckRule file>: its top-level id
  local id
  id=$(awk '/^id:/ {print $2; exit}' "$1")
  [[ "$id" =~ ^[0-9a-f-]{36}$ ]] || die "$1 has no top-level id"
  echo "$id"
}

dashboard_id() {  # dashboard_id <Dashboard file>: its metadata.dash0Extensions.id
  local id
  id=$(awk '/dash0Extensions:/ {found = 1} found && $1 == "id:" {print $2; exit}' "$1")
  [[ "$id" =~ ^[0-9a-f-]{36}$ ]] || die "$1 has no metadata.dash0Extensions.id"
  echo "$id"
}

automation_body() {  # automation_body <trigger rule id>: the automation definition with its prompt inserted
  # An automation without a rule list runs for every failed check, so insist on exactly the trigger rule.
  jq -e --arg rule "$1" '.spec.triggers[0].spec.checkRuleIDs == [$rule]' "$AUTOMATION.json" >/dev/null \
    || die "$AUTOMATION.json must trigger on check rule $1, the id in $TRIGGER_RULE"
  [[ -s "$AUTOMATION.prompt.md" ]] || die "$AUTOMATION.prompt.md is missing or empty"
  jq --rawfile prompt "$AUTOMATION.prompt.md" '.spec.prompt.user = $prompt' "$AUTOMATION.json"
}

report() {  # report <automation origin>: read each asset back from Dash0 and print its name and id
  local origin="$1" file id
  dash0 -X api GET "/api/datasets/$DATASET" --dataset "" \
    | jq -r '"Dataset \"\(.metadata.name)\": semantic convention upgrades \(.spec.processing.semanticConventionUpgrades)"'
  # A read by id doesn't echo the id back, so print the one that was asked for.
  for file in "$ASSETS"/dashboards/*.yaml; do
    id=$(dashboard_id "$file")
    api GET "/api/dashboards/$id" | jq -r --arg id "$id" '"Dashboard \"\(.spec.display.name)\" (\($id))"'
  done
  for file in "$ASSETS"/checks/*.yaml; do
    id=$(rule_id "$file")
    api GET "/api/alerting/check-rules/$id" \
      | jq -r --arg id "$id" '"Check rule \"\(.name)\" (\($id)), enabled: \(.enabled // true)"'
  done
  api GET "/api/agentic-workflows/$origin" \
    | jq -r '"Agent0 automation \"\(.spec.display.name)\" (\(.metadata.labels["dash0.com/id"])), enabled: \(.spec.enabled),"
        + " triggered by check rule \(.spec.triggers[0].spec.checkRuleIDs | join(", "))"'
}

main() {
  local dry_run=false
  case "${1:-}" in
    "") ;;
    --dry-run) dry_run=true ;;
    *) die "usage: scripts/dash0-apply.sh [--dry-run]" ;;
  esac
  require

  local rule body origin prompt_length
  rule=$(rule_id "$TRIGGER_RULE")
  body=$(automation_body "$rule")
  origin=$(jq -r '.metadata.labels["dash0.com/origin"] // empty' <<<"$body")
  [[ -n "$origin" ]] || die "$AUTOMATION.json has no dash0.com/origin label"

  # -X: the CLI treats Dash0Dataset documents (dataset.yaml) and `dash0 api` as experimental.
  if $dry_run; then
    dash0 -X apply -f "$ASSETS" --dataset "$DATASET" --dry-run
    prompt_length=$(jq -r '.spec.prompt.user | length' <<<"$body")
    echo "Dry run: would upsert Agent0 automation $origin with a $prompt_length-character prompt"
    return 0
  fi

  dash0 -X apply -f "$ASSETS" --dataset "$DATASET"
  api PUT "/api/agentic-workflows/$origin" -f - <<<"$body" \
    | jq -r '"Agent0 automation \"\(.spec.display.name)\" (\(.metadata.labels["dash0.com/id"])) upserted"'
  echo
  echo "In Dash0 now:"
  report "$origin"
}

main "$@"; exit
