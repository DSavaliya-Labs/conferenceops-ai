#!/usr/bin/env bash
# Tests for scripts/dash0-apply.sh. A fake dash0 executable stands in for the Dash0 CLI and API. Each case runs a copy
# of the script next to a copy of observability/dash0/ (the script finds its assets relative to itself), with a PATH
# that holds only the tools the script needs, so no Dash0 credentials, network access or dataset are used.
#   Usage: bash scripts/tests/dash0-apply.test.sh        Exits non-zero when a check fails.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd "$HERE/../.." && pwd -P)
AUTOMATION="observability/dash0/automations/discovery-capacity-fix"  # .json and .prompt.md, relative to a repo copy
ORIGIN="dash0-cli_conferenceops-discovery-capacity-fix"
OTHER_RULE="8fed603d-1a59-400b-bfa8-d46c8162427d"  # "Search is slow", which must never start the automation
TOKEN="fake-dash0-token-that-must-never-be-printed"
WORKFLOW='Agent0 automation "ConferenceOps: fix discovery capacity" (fake-workflow-id)'  # as the script prints it
RULE="" WORK="" BIN="" CASE="" CURRENT="" RC=0 PASSED=0 FAILED=0

# --- The fake dash0 -------------------------------------------------------------------------------------------------

write_fake() {
  mkdir -p "$WORK/fakes"
  cat >"$WORK/fakes/dash0" <<'EOF'
#!/usr/bin/env bash
# Fake dash0: records each call's arguments on one line of FAKE_DASH0_LOG and answers like the Dash0 CLI and API.
#   -X apply -f <dir> ...    prints a line; changes nothing
#   -X api PUT <path> -f -   keeps the body from stdin in FAKE_DASH0_PUT and returns it as stored. When
#                            FAKE_DASH0_STORED_ENABLED is set, Dash0 stores that spec.enabled instead (an API that
#                            ignored the field)
#   -X api GET <path>        the dataset, the dashboard, a check rule (FAKE_DASH0_RULE_ENABLED: true, false or
#                            absent) or the automation stored last
set -euo pipefail
printf '%s\n' "$*" >>"$FAKE_DASH0_LOG"
[[ -n "${DASH0_API_URL:-}" && -n "${DASH0_AUTH_TOKEN:-}" ]] || { echo "fake dash0: no credentials" >&2; exit 1; }
if [[ "${1:-}" == -X ]]; then shift; fi
case "${1:-} ${2:-}" in
  "apply -f")
    if [[ " $* " == *" --dry-run "* ]]; then echo "fake dash0: valid"; else echo "fake dash0: applied"; fi
    ;;
  "api PUT")
    cat >"$FAKE_DASH0_PUT"
    jq --arg stored "${FAKE_DASH0_STORED_ENABLED:-}" \
      '.metadata.labels["dash0.com/id"] = "fake-workflow-id"
       | if $stored == "" then . else .spec.enabled = ($stored == "true") end' "$FAKE_DASH0_PUT" >"$FAKE_DASH0_STORED"
    cat "$FAKE_DASH0_STORED"
    ;;
  "api GET")
    case "${3:-}" in
      /api/datasets/*)
        echo '{"metadata":{"name":"ConferenceOps AI"},"spec":{"processing":{"semanticConventionUpgrades":"v1.36.0"}}}'
        ;;
      /api/dashboards/*) echo '{"spec": {"display": {"name": "ConferenceOps: attendee impact"}}}' ;;
      /api/alerting/check-rules/*)
        if [[ "${FAKE_DASH0_RULE_ENABLED:-true}" == absent ]]; then
          echo '{"name": "Fake rule"}'
        else
          echo "{\"name\": \"Fake rule\", \"enabled\": ${FAKE_DASH0_RULE_ENABLED:-true}}"
        fi
        ;;
      /api/agentic-workflows/*) cat "$FAKE_DASH0_STORED" ;;
      *) echo "fake dash0: unexpected GET ${3:-}" >&2; exit 1 ;;
    esac
    ;;
  *)
    echo "fake dash0: unexpected call: $*" >&2
    exit 1
    ;;
esac
EOF
  chmod +x "$WORK/fakes/dash0"
}

# A PATH directory that holds only the given tools: real ones, or the fake dash0.
make_bin() {  # make_bin <dir> <tool>...
  local dir="$1" tool
  shift
  mkdir -p "$dir"
  for tool in "$@"; do
    case "$tool" in
      dash0) cp "$WORK/fakes/dash0" "$dir/dash0" ;;
      bash) ln -s "$BASH" "$dir/bash" ;;
      *) ln -s "$(command -v "$tool")" "$dir/$tool" ;;
    esac
  done
}

# --- Harness --------------------------------------------------------------------------------------------------------

begin() {  # begin <case>: a fresh copy of the script and the assets, credentials set, the fake reset
  CURRENT="$1"
  CASE="$WORK/cases/$1"
  mkdir -p "$CASE/repo/scripts" "$CASE/repo/observability"
  cp "$REPO_ROOT/scripts/dash0-apply.sh" "$CASE/repo/scripts/"
  cp -R "$REPO_ROOT/observability/dash0" "$CASE/repo/observability/"
  export FAKE_DASH0_LOG="$CASE/dash0.args" FAKE_DASH0_PUT="$CASE/put.json" FAKE_DASH0_STORED="$CASE/stored.json"
  export DASH0_API_URL="https://api.dash0.invalid" DASH0_AUTH_TOKEN="$TOKEN"
  unset FAKE_DASH0_STORED_ENABLED FAKE_DASH0_RULE_ENABLED
  : >"$FAKE_DASH0_LOG"
}

edit_automation() {  # edit_automation <jq filter>: change this case's copy of the automation definition
  local file="$CASE/repo/$AUTOMATION.json"
  jq "$1" "$file" >"$file.new"
  mv "$file.new" "$file"
}

run() {  # run <args>...: the case's copy of the script with only $BIN on PATH; sets RC, $CASE/stdout and $CASE/stderr
  RC=0
  (cd "$CASE" && PATH="$BIN" "$CASE/repo/scripts/dash0-apply.sh" "$@") >"$CASE/stdout" 2>"$CASE/stderr" || RC=$?
}

pass() { PASSED=$((PASSED + 1)); }

fail() {
  FAILED=$((FAILED + 1))
  printf 'FAIL [%s] %s\n' "$CURRENT" "$*" >&2
}

expect_rc() {
  if [[ "$RC" == "$1" ]]; then pass; else fail "exit $RC, expected $1. stderr: $(head -c 600 "$CASE/stderr")"; fi
}

contains() {  # contains <file> <text>
  if grep -qF -- "$2" "$1"; then pass; else fail "${1##*/} lacks: $2"; fi
}

lacks() {  # lacks <file> <text>
  if grep -qF -- "$2" "$1"; then fail "${1##*/} contains: $2"; else pass; fi
}

has_line() {  # has_line <file> <the whole line>
  if grep -qxF -- "$2" "$1"; then pass; else fail "${1##*/} has no line: $2. It has: $(head -c 600 "$1")"; fi
}

is_empty() {  # is_empty <file>
  if [[ -s "$1" ]]; then fail "${1##*/} is not empty: $(head -c 300 "$1")"; else pass; fi
}

line_count() {  # line_count <file> <n>
  local n
  n=$(wc -l <"$1" | tr -d ' ')
  if [[ "$n" == "$2" ]]; then pass; else fail "${1##*/} has $n lines, expected $2: $(head -c 600 "$1")"; fi
}

sent() {  # sent <jq test> <what failed>: the automation body the script sent passes the test
  if [[ -s "$FAKE_DASH0_PUT" ]] && jq -e "$1" "$FAKE_DASH0_PUT" >/dev/null 2>&1; then pass; else fail "$2"; fi
}

# --- Tests ----------------------------------------------------------------------------------------------------------

test_needs_credentials() {
  begin no-api-url
  unset DASH0_API_URL
  run
  expect_rc 1
  contains "$CASE/stderr" "DASH0_API_URL is not set"
  is_empty "$FAKE_DASH0_LOG"

  begin no-token
  unset DASH0_AUTH_TOKEN
  run --dry-run
  expect_rc 1
  contains "$CASE/stderr" "DASH0_AUTH_TOKEN is not set"
  is_empty "$FAKE_DASH0_LOG"
}

test_rejects_unknown_arguments() {
  begin unknown-argument
  run --force
  expect_rc 1
  contains "$CASE/stderr" "usage: scripts/dash0-apply.sh [--dry-run] [--arm]"
  is_empty "$FAKE_DASH0_LOG"

  begin extra-argument
  run --dry-run --arm now
  expect_rc 1
  contains "$CASE/stderr" "usage: scripts/dash0-apply.sh [--dry-run] [--arm]"
  is_empty "$FAKE_DASH0_LOG"
}

refuses_trigger() {  # refuses_trigger <case> <jq filter> [script args]: that definition stops the script first
  local name="$1" filter="$2"
  shift 2
  begin "$name"
  edit_automation "$filter"
  run "$@"
  expect_rc 1
  contains "$CASE/stderr" "discovery-capacity-fix.json must"
  is_empty "$FAKE_DASH0_LOG"
}

test_only_the_rejection_rule_starts_the_automation() {
  refuses_trigger wrong-rule ".spec.triggers[0].spec.checkRuleIDs = [\"$OTHER_RULE\"]"
  refuses_trigger no-rule-list 'del(.spec.triggers[0].spec.checkRuleIDs)'
  refuses_trigger second-rule ".spec.triggers[0].spec.checkRuleIDs += [\"$OTHER_RULE\"]"
  refuses_trigger extra-unrestricted-trigger '.spec.triggers += [{"kind": "failed_check.new", "spec": {}}]'
  refuses_trigger extra-schedule-trigger '.spec.triggers += [{"kind": "schedule", "spec": {"cron": "*/5 * * * *"}}]'
  refuses_trigger wrong-trigger-kind '.spec.triggers[0].kind = "slack_bot.failed_check.new"'
  refuses_trigger no-trigger '.spec.triggers = []'
  refuses_trigger extra-trigger-on-dry-run '.spec.triggers += [{"kind": "failed_check.new", "spec": {}}]' --dry-run
}

test_dry_run_changes_nothing() {
  begin dry-run
  run --dry-run
  expect_rc 0
  has_line "$FAKE_DASH0_LOG" "-X apply -f $CASE/repo/observability/dash0 --dataset conferenceops --dry-run"
  line_count "$FAKE_DASH0_LOG" 1  # only the validating apply: no API call, so no PUT
  lacks "$FAKE_DASH0_LOG" "api PUT"
  contains "$CASE/stdout" "Dry run: would upsert Agent0 automation $ORIGIN disarmed (spec.enabled false)"

  begin dry-run-arm
  run --arm --dry-run
  expect_rc 0
  line_count "$FAKE_DASH0_LOG" 1
  lacks "$FAKE_DASH0_LOG" "api PUT"
  contains "$CASE/stdout" "Dry run: would upsert Agent0 automation $ORIGIN armed (spec.enabled true)"
}

test_apply_sends_the_automation_disarmed() {
  begin apply
  run
  expect_rc 0
  has_line "$FAKE_DASH0_LOG" "-X apply -f $CASE/repo/observability/dash0 --dataset conferenceops"
  has_line "$FAKE_DASH0_LOG" "-X api PUT /api/agentic-workflows/$ORIGIN -f - --dataset conferenceops"
  sent '.spec.enabled == false' "without --arm, the automation was not sent with spec.enabled false"
  if jq -e --rawfile prompt "$CASE/repo/$AUTOMATION.prompt.md" '.spec.prompt.user == $prompt' "$FAKE_DASH0_PUT" \
    >/dev/null 2>&1; then pass; else fail "the prompt sent is not the .prompt.md file"; fi
  if jq -e --slurpfile def "$CASE/repo/$AUTOMATION.json" \
    'del(.spec.prompt, .spec.enabled) == ($def[0] | del(.spec.prompt, .spec.enabled))' "$FAKE_DASH0_PUT" \
    >/dev/null 2>&1; then pass; else fail "the body sent differs from the definition beyond the prompt and enabled"; fi
  contains "$CASE/stdout" "$WORKFLOW upserted, disarmed"
  contains "$CASE/stdout" "$WORKFLOW, enabled: false, triggered by check rule $RULE"
  contains "$CASE/stdout" "Arm it right before the incident run: scripts/dash0-apply.sh --arm"

  # A definition edited to enabled true still goes out disarmed: only --arm arms it.
  begin apply-definition-enabled
  edit_automation '.spec.enabled = true'
  run
  expect_rc 0
  sent '.spec.enabled == false' "a definition with enabled true was sent armed without --arm"
}

test_arm_sends_the_automation_enabled() {
  begin arm
  run --arm
  expect_rc 0
  sent '.spec.enabled == true' "--arm did not send the automation with spec.enabled true"
  contains "$CASE/stdout" "$WORKFLOW upserted, armed"
  contains "$CASE/stdout" "$WORKFLOW, enabled: true, triggered by check rule $RULE"
  contains "$CASE/stdout" "ARMED:"
}

test_fails_when_dash0_stores_another_state() {
  begin stored-armed
  export FAKE_DASH0_STORED_ENABLED=true
  run
  expect_rc 1
  contains "$CASE/stderr" "Dash0 did not store the automation disarmed: its spec.enabled is true"

  begin stored-disarmed
  export FAKE_DASH0_STORED_ENABLED=false
  run --arm
  expect_rc 1
  contains "$CASE/stderr" "Dash0 did not store the automation armed: its spec.enabled is false"
}

test_read_back_reports_check_rules_as_dash0_has_them() {
  begin rule-disabled
  export FAKE_DASH0_RULE_ENABLED=false
  run
  expect_rc 0
  contains "$CASE/stdout" "Check rule \"Fake rule\" ($RULE), enabled: false"
  lacks "$CASE/stdout" "enabled: true"

  begin rule-enabled
  export FAKE_DASH0_RULE_ENABLED=true
  run
  expect_rc 0
  contains "$CASE/stdout" "Check rule \"Fake rule\" ($RULE), enabled: true"

  # Dash0 defaults a check rule without the field to enabled.
  begin rule-enabled-by-default
  export FAKE_DASH0_RULE_ENABLED=absent
  run
  expect_rc 0
  contains "$CASE/stdout" "Check rule \"Fake rule\" ($RULE), enabled: true"
}

test_never_prints_the_token() {
  begin token
  run --arm
  expect_rc 0
  lacks "$CASE/stdout" "$TOKEN"
  lacks "$CASE/stderr" "$TOKEN"
  lacks "$FAKE_DASH0_LOG" "$TOKEN"
}

main() {
  command -v jq >/dev/null || { echo "These tests need jq." >&2; return 1; }
  WORK=$(cd "$(mktemp -d "${TMPDIR:-/tmp}/dash0-apply-test.XXXXXX")" && pwd -P)
  trap 'rm -rf -- "$WORK"' EXIT
  RULE=$(awk '/^id:/ {print $2; exit}' "$REPO_ROOT/observability/dash0/checks/discovery-rejecting-searches.yaml")
  write_fake
  BIN="$WORK/bin"
  make_bin "$BIN" bash awk cat dirname jq dash0

  test_needs_credentials
  test_rejects_unknown_arguments
  test_only_the_rejection_rule_starts_the_automation
  test_dry_run_changes_nothing
  test_apply_sends_the_automation_disarmed
  test_arm_sends_the_automation_enabled
  test_fails_when_dash0_stores_another_state
  test_read_back_reports_check_rules_as_dash0_has_them
  test_never_prints_the_token

  echo "dash0-apply tests: $PASSED checks passed, $FAILED failed."
  ((FAILED == 0))
}

main "$@"; exit
