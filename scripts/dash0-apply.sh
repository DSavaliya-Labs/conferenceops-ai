#!/usr/bin/env bash
# Apply everything in observability/dash0/ to the Dash0 dataset `conferenceops`, idempotently:
#   - `dash0 apply`: the dataset's processing settings, the dashboard and the check rules. Each carries a fixed id,
#     so a re-run updates them in place instead of creating copies.
#   - the automations API (`dash0 apply` has no automation kind): the Agent0 automation, upserted by its
#     `dash0.com/origin` label, with its prompt read from the .prompt.md file next to its definition.
# The automation goes out disarmed (spec.enabled false) unless --arm is given, so a routine re-apply never arms it:
# armed, it answers a failure of the rejection check with an incident pull request that merges without a human.
# Then it reads every asset back from Dash0 and prints its name and id.
#   Usage: scripts/dash0-apply.sh [--dry-run] [--arm]
#     --dry-run  validates everything and changes nothing.
#     --arm      enables the Agent0 automation. Without it, every run sends it disabled.
#   Needs DASH0_API_URL and DASH0_AUTH_TOKEN in the environment (. ~/.config/conferenceops/env.sh), the dash0 CLI
#   (1.20 or later) and jq. The CLI reads the token from the environment; this script never prints it.
#   Tests: scripts/tests/dash0-apply.test.sh
set -euo pipefail

DATASET="conferenceops"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ASSETS="$ROOT/observability/dash0"
AUTOMATION="$ASSETS/automations/discovery-capacity-fix"  # .json (definition) and .prompt.md (prompt)
TRIGGER_RULE="$ASSETS/checks/discovery-rejecting-searches.yaml"
USAGE="usage: scripts/dash0-apply.sh [--dry-run] [--arm]"

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

automation_body() {  # automation_body <trigger rule id> <true|false>: the definition, with its prompt and spec.enabled
  # The automation runs for every trigger it lists, and a failed-check trigger without a rule list runs for every
  # failed check. So insist on exactly one trigger: a new failure of the rejection rule, and of no other rule.
  jq -e --arg rule "$1" '
      (.spec.triggers | length) == 1
      and .spec.triggers[0].kind == "failed_check.new"
      and .spec.triggers[0].spec.checkRuleIDs == [$rule]' "$AUTOMATION.json" >/dev/null \
    || die "$AUTOMATION.json must have exactly one trigger: failed_check.new on check rule $1 only," \
      "the id in $TRIGGER_RULE"
  [[ -s "$AUTOMATION.prompt.md" ]] || die "$AUTOMATION.prompt.md is missing or empty"
  jq --rawfile prompt "$AUTOMATION.prompt.md" --argjson enabled "$2" \
    '.spec.prompt.user = $prompt | .spec.enabled = $enabled' "$AUTOMATION.json"
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
  # A check rule without `enabled` is enabled. Not `.enabled // true`: jq's `//` replaces false as well as null.
  for file in "$ASSETS"/checks/*.yaml; do
    id=$(rule_id "$file")
    api GET "/api/alerting/check-rules/$id" | jq -r --arg id "$id" \
      '"Check rule \"\(.name)\" (\($id)), enabled: \(if .enabled == null then true else .enabled end)"'
  done
  api GET "/api/agentic-workflows/$origin" \
    | jq -r '"Agent0 automation \"\(.spec.display.name)\" (\(.metadata.labels["dash0.com/id"])), enabled: \(.spec.enabled),"
        + " triggered by check rule \(.spec.triggers[0].spec.checkRuleIDs | join(", "))"'
}

main() {
  local dry_run=false armed=false arg
  for arg in "$@"; do
    case "$arg" in
      --dry-run) dry_run=true ;;
      --arm) armed=true ;;
      *) die "$USAGE" ;;
    esac
  done
  require

  local rule body origin state response prompt_length
  rule=$(rule_id "$TRIGGER_RULE")
  body=$(automation_body "$rule" "$armed")
  origin=$(jq -r '.metadata.labels["dash0.com/origin"] // empty' <<<"$body")
  [[ -n "$origin" ]] || die "$AUTOMATION.json has no dash0.com/origin label"
  if $armed; then state=armed; else state=disarmed; fi

  # -X: the CLI treats Dash0Dataset documents (dataset.yaml) and `dash0 api` as experimental.
  if $dry_run; then
    dash0 -X apply -f "$ASSETS" --dataset "$DATASET" --dry-run
    prompt_length=$(jq -r '.spec.prompt.user | length' <<<"$body")
    echo "Dry run: would upsert Agent0 automation $origin $state (spec.enabled $armed)" \
      "with a $prompt_length-character prompt"
    return 0
  fi

  dash0 -X apply -f "$ASSETS" --dataset "$DATASET"
  response=$(api PUT "/api/agentic-workflows/$origin" -f - <<<"$body")
  jq -e --argjson enabled "$armed" '.spec.enabled == $enabled' <<<"$response" >/dev/null \
    || die "Dash0 did not store the automation $state: its spec.enabled is $(jq -c '.spec.enabled' <<<"$response")"
  jq -r --arg state "$state" <<<"$response" \
    '"Agent0 automation \"\(.spec.display.name)\" (\(.metadata.labels["dash0.com/id"])) upserted, \($state)"'
  echo
  echo "In Dash0 now:"
  report "$origin"
  echo
  if $armed; then
    echo "ARMED: a new failure of \"Discovery is rejecting searches\" starts Agent0, and its incident pull request can"
    echo "merge and deploy without a human. Disarm it when the incident run is over: scripts/dash0-apply.sh"
  else
    echo "The Agent0 automation is disarmed. Arm it right before the incident run: scripts/dash0-apply.sh --arm"
  fi
}

main "$@"; exit
