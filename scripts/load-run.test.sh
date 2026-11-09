#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/load-run.sh"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

assert_equal() {
  local expected="$1" actual="$2" label="$3"
  [[ "$actual" == "$expected" ]] || fail "$label: expected '$expected', got '$actual'"
}

assert_duration() {
  local input="$1" expected="$2" actual
  actual=$(duration_seconds "$input") || fail "duration_seconds rejected $input"
  assert_equal "$expected" "$actual" "duration_seconds $input"
}

assert_duration 90s 90
assert_duration 5m 300
assert_duration 1h30m 5400
assert_duration 0003m 180

for invalid in '' 0s 0m 30m1h 1s2m '5 minutes'; do
  if duration_seconds "$invalid" >/dev/null 2>&1; then
    fail "duration_seconds accepted invalid value '$invalid'"
  fi
done

status=0
main --duration 0s >/dev/null 2>&1 || status=$?
assert_equal 2 "$status" 'main rejects a zero duration'
status=0
main --rate 0 >/dev/null 2>&1 || status=$?
assert_equal 2 "$status" 'main rejects a zero rate'

status=0
finish Complete 0 >/dev/null 2>&1 || status=$?
assert_equal 0 "$status" 'finish preserves a successful k6 exit code'
status=0
finish Failed 99 >/dev/null 2>&1 || status=$?
assert_equal 99 "$status" 'finish preserves a threshold failure exit code'
status=0
finish Failed 105 >/dev/null 2>&1 || status=$?
assert_equal 105 "$status" 'finish preserves an early-stop exit code'
status=0
finish Failed '' >/dev/null 2>&1 || status=$?
assert_equal 1 "$status" 'finish rejects a missing k6 exit code'

RESULTS_DIR=$(mktemp -d)
trap 'rm -rf "$RESULTS_DIR"' EXIT
CONTEXT=test-conferenceops
NAMESPACE=conferenceops
JOB=conferenceops-load
kubectl() {
  if [[ "${5:-}" == logs ]]; then
    printf 'summary evidence\n'
  else
    return 1
  fi
}

saved=$(save_logs 12345678-abcd search-peak-previous)
assert_equal "$RESULTS_DIR/search-peak-previous-12345678.log" "$saved" 'save_logs path'
assert_equal 'summary evidence' "$(<"$saved")" 'save_logs contents'
already_saved 12345678-abcd || fail 'already_saved did not find saved Job logs'

help=$(bash "$ROOT/scripts/load-run.sh" --help 2>&1) || fail 'executing load-run.sh --help failed'
[[ "$help" == *'Usage: scripts/load-run.sh'* ]] || fail 'direct execution did not reach main'

printf 'load-run tests passed\n'
