#!/usr/bin/env bash
# Tests for scripts/check-incident-scope.sh (OPS-1). Each case commits a base and a head version of
# deploy/env/aks/values.yaml in a throwaway git repository and runs the check from its root, the way the
# incident-scope CI job does. Needs git and ruby, and touches nothing outside a temporary directory.
#   Usage: bash scripts/tests/check-incident-scope.test.sh        Exits non-zero when a check fails.
#   CHECK_SCRIPT=<path> runs the same cases against another copy of the script, to see which cases catch a bug.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
SCRIPT="${CHECK_SCRIPT:-$HERE/../check-incident-scope.sh}"
VALUES="deploy/env/aks/values.yaml"
WORK="" CASE="" REPO="" CURRENT="" BASE="" BIN_NO_RUBY="" RC=0 PASSED=0 FAILED=0

# --- Fixtures -------------------------------------------------------------------------------------------------------

DISCOVERY_BLOCK='  discovery:
    replicas: 1
    resources:
      requests: {cpu: 250m, memory: 256Mi}
      limits: {memory: 512Mi}'

# The AKS values in the shape the deploy PR (#5) gives them: a test input, not the deployed file.
base_values() {
  cat <<EOF
# Values deployed to AKS. Incident pull requests may change only the discovery replicas or resources (OPS-1).

environment: aks

public:
  enabled: true
  dnsLabel: conferenceops-ai

components:
$DISCOVERY_BLOCK
EOF
}

# --- Harness --------------------------------------------------------------------------------------------------------

repo_git() {  # git in the case's repository, with a fixed identity and no signing or hooks
  git -C "$REPO" -c user.name="incident-scope test" -c user.email=test@example.invalid -c commit.gpgsign=false \
    -c core.hooksPath=/dev/null "$@"
}

commit() {  # commit <message>: everything in the working tree
  repo_git add -A
  repo_git commit -q -m "$1"
}

begin() {  # begin <case>: a fresh repository whose first commit, on main, holds the base values
  CURRENT="$1"
  CASE="$WORK/cases/$1"
  REPO="$CASE/repo"
  mkdir -p "$REPO/${VALUES%/*}"
  repo_git init -q -b main
  base_values >"$REPO/$VALUES"
  commit "Base values"
  BASE=$(repo_git rev-parse HEAD)
}

replace() {  # replace <text> <with>: edit the values file. A missing <text> is a broken test, so it stops the run.
  local content
  content=$(<"$REPO/$VALUES")
  if [[ "$content" != *"$1"* ]]; then
    echo "BROKEN TEST [$CURRENT]: the values file doesn't contain: $1" >&2
    exit 1
  fi
  # Prefix and suffix around the first match, not ${content/.../...}: bash 3.2 keeps quotes in that replacement.
  printf '%s%s%s\n' "${content%%"$1"*}" "$2" "${content#*"$1"}" >"$REPO/$VALUES"
}

run() {  # run [NAME=value]...: the check from the repository root, as the CI job runs it; sets RC and $CASE/out
  RC=0
  (cd "$REPO" && env IS_INCIDENT=true BASE_SHA="$BASE" "$@" bash "$SCRIPT") >"$CASE/out" 2>&1 || RC=$?
}

make_bin() {  # make_bin <dir> <tool>...: a PATH directory that holds only these tools
  local dir="$1" tool
  shift
  mkdir -p "$dir"
  for tool in "$@"; do
    if [[ "$tool" == bash ]]; then ln -s "$BASH" "$dir/bash"; else ln -s "$(command -v "$tool")" "$dir/$tool"; fi
  done
}

pass() { PASSED=$((PASSED + 1)); }

fail() {
  FAILED=$((FAILED + 1))
  printf 'FAIL [%s] %s\n' "$CURRENT" "$*" >&2
}

expect_rc() {  # expect_rc <code>
  if [[ "$RC" == "$1" ]]; then pass; else fail "exit $RC, expected $1. Output: $(head -c 600 "$CASE/out")"; fi
}

contains() {  # contains <text>: the check printed it
  if grep -qF -- "$1" "$CASE/out"; then pass; else fail "output lacks: $1"; fi
}

lacks() {  # lacks <text>: the check didn't print it
  if grep -qF -- "$1" "$CASE/out"; then fail "output contains: $1"; else pass; fi
}

# --- Tests ----------------------------------------------------------------------------------------------------------

test_not_an_incident() {
  begin not-an-incident
  replace "environment: aks" "environment: dev"
  commit "Head values"
  run IS_INCIDENT=false
  expect_rc 0
  contains "Not an incident pull request"
}

test_more_discovery_replicas() {  # the planned incident fix
  begin more-replicas
  replace "    replicas: 1" "    replicas: 3"
  commit "Head values"
  run
  expect_rc 0
  contains "Incident scope OK"
}

test_more_discovery_resources() {
  begin more-resources
  replace "requests: {cpu: 250m, memory: 256Mi}" "requests: {cpu: 500m, memory: 512Mi}"
  replace "limits: {memory: 512Mi}" "limits: {memory: 1Gi}"
  commit "Head values"
  run
  expect_rc 0
  contains "Incident scope OK"
}

test_reformatted_discovery() {  # the same values as one flow mapping, plus replicas: what counts is the meaning
  begin reformatted
  replace "$DISCOVERY_BLOCK" \
    "  discovery: {replicas: 3, resources: {requests: {cpu: 250m, memory: 256Mi}, limits: {memory: 512Mi}}}"
  commit "Head values"
  run
  expect_rc 0
  contains "Incident scope OK"
}

test_flow_mapping_cannot_hide_a_field() {  # regression: the line check alone let this through
  begin flow-mapping-hides-command
  local flow="  discovery: {replicas: 3, command: worker,"
  flow+=" resources: {requests: {cpu: 250m, memory: 256Mi}, limits: {memory: 512Mi}}}"
  replace "$DISCOVERY_BLOCK" "$flow"
  commit "Head values"
  run
  expect_rc 1
  contains 'added: ["components","discovery","command"]'
  lacks 'removed: ["components","discovery"]'
  lacks "Incident scope OK"
}

test_field_the_chart_does_not_read() {  # the chart reads replicas; replicaCount would scale nothing
  begin replica-count
  replace "    replicas: 1" "    replicas: 1
    replicaCount: 3"
  commit "Head values"
  run
  expect_rc 1
  contains "Changed line outside replicas/resources:     replicaCount: 3"
  contains 'added: ["components","discovery","replicaCount"]'
}

test_admission_limit() {  # the incident fix never changes the admission capacity (PLAN.md §15.2)
  begin admission-limit
  replace "    replicas: 1" "    replicas: 1
    maxInflight: 8"
  commit "Base values with the admission limit"
  BASE=$(repo_git rev-parse HEAD)
  replace "maxInflight: 8" "maxInflight: 40"
  commit "Head values"
  run
  expect_rc 1
  contains "Changed line outside replicas/resources:     maxInflight: 40"
  contains 'changed: ["components","discovery","maxInflight"]'
}

test_more_replicas_next_to_the_admission_limit() {  # the planned fix still passes with the limit in the values
  begin replicas-next-to-limit
  replace "    replicas: 1" "    replicas: 1
    maxInflight: 8"
  commit "Base values with the admission limit"
  BASE=$(repo_git rev-parse HEAD)
  replace "    replicas: 1" "    replicas: 3"
  commit "Head values"
  run
  expect_rc 0
  contains "Incident scope OK"
}

test_allowed_key_at_another_path() {  # an allowed key name outside components.discovery
  begin key-at-another-path
  replace "  dnsLabel: conferenceops-ai" "  dnsLabel: conferenceops-ai
  replicas: 2"
  commit "Head values"
  run
  expect_rc 1
  contains 'added: ["public","replicas"]'
}

test_other_value() {
  begin other-value
  replace "dnsLabel: conferenceops-ai" "dnsLabel: someone-else"
  commit "Head values"
  run
  expect_rc 1
  contains "Changed line outside replicas/resources: "
  contains 'changed: ["public","dnsLabel"]'
}

test_other_file() {
  begin other-file
  replace "    replicas: 1" "    replicas: 3"
  echo "rate: 1000" >"$REPO/load.yaml"
  commit "Head values"
  run
  expect_rc 1
  contains "file=load.yaml::An incident PR may only change $VALUES"
}

test_comment_only() {
  begin comment-only
  replace "components:" "# Discovery scales out under load.
components:"
  commit "Head values"
  run
  expect_rc 0
  contains "Incident scope OK"
}

test_deleted_values_file() {
  begin deleted-values
  rm "$REPO/$VALUES"
  commit "Head values"
  run
  expect_rc 1
  contains "An incident PR may not delete $VALUES"
}

test_unparsable_values() {  # the line check passes it, but the values can't be loaded
  begin unparsable
  replace "    replicas: 1" "    replicas: [3"
  commit "Head values"
  run
  expect_rc 1
  contains "Couldn't load $VALUES"
  contains "head.yaml: "
}

test_on_a_ci_merge_commit() {  # CI checks out GitHub's merge commit, and main moved on after the branch was cut
  begin ci-merge-commit
  repo_git switch -q -c incident
  replace "    replicas: 1" "    replicas: 3"
  commit "Scale discovery to 3 replicas"
  repo_git switch -q main
  echo "notes" >"$REPO/NOTES.md"
  commit "Main moves on"
  BASE=$(repo_git rev-parse HEAD)
  repo_git merge -q --no-ff -m "Merge incident into main" incident
  run
  expect_rc 0
  contains "Incident scope OK"
}

test_needs_base_sha() {
  begin no-base-sha
  run BASE_SHA=
  expect_rc 1
  contains "BASE_SHA is not set"
}

test_needs_ruby() {  # without ruby the values can't be compared, so the check fails closed
  begin no-ruby
  replace "    replicas: 1" "    replicas: 3"
  commit "Head values"
  run PATH="$BIN_NO_RUBY"
  expect_rc 1
  contains "ruby isn't installed"
}

main() {
  command -v ruby >/dev/null || { echo "These tests need ruby." >&2; return 1; }
  WORK=$(cd "$(mktemp -d "${TMPDIR:-/tmp}/check-incident-scope-test.XXXXXX")" && pwd -P)
  trap 'rm -rf -- "$WORK"' EXIT
  BIN_NO_RUBY="$WORK/bin/no-ruby"
  make_bin "$BIN_NO_RUBY" bash cat git grep mktemp rm sed

  test_not_an_incident
  test_more_discovery_replicas
  test_more_discovery_resources
  test_reformatted_discovery
  test_flow_mapping_cannot_hide_a_field
  test_field_the_chart_does_not_read
  test_admission_limit
  test_more_replicas_next_to_the_admission_limit
  test_allowed_key_at_another_path
  test_other_value
  test_other_file
  test_comment_only
  test_deleted_values_file
  test_unparsable_values
  test_on_a_ci_merge_commit
  test_needs_base_sha
  test_needs_ruby

  echo "check-incident-scope tests: $PASSED checks passed, $FAILED failed."
  ((FAILED == 0))
}

main "$@"; exit
