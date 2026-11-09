#!/usr/bin/env bash
# Tests for scripts/dash0-deploy-marker.sh. A fake dash0 executable stands in for the Dash0 CLI and records every
# call, so no Dash0 credentials, network access or dataset are used. Each case runs the script with a PATH that
# holds only bash and the fake, and the CLI's environment set the way the deploy workflow sets it.
#   Usage: bash scripts/tests/dash0-deploy-marker.test.sh        Exits non-zero when a check fails.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
SCRIPT="$HERE/../dash0-deploy-marker.sh"
TAG="0123456789abcdef0123456789abcdef01234567"
TOKEN="fake-dash0-token-that-must-never-be-printed"
REPOSITORY_URL="https://github.example/acme/conferenceops-ai"
SERVICES=(web core-api discovery-api worker)  # every service the chart deploys, in the script's order
WORK="" BIN="" BIN_NO_DASH0="" RUN_PATH="" CASE="" CURRENT="" RC=0 PASSED=0 FAILED=0

# --- The fake dash0 -------------------------------------------------------------------------------------------------

write_fake() {
  mkdir -p "$WORK/fakes"
  cat >"$WORK/fakes/dash0" <<'EOF'
#!/usr/bin/env bash
# Fake dash0: records each call's arguments on one line of FAKE_DASH0_LOG, insists on the CLI's environment, and
# refuses the event whose service.name is FAKE_DASH0_REFUSE_SERVICE, as a failed ingest would.
set -euo pipefail
printf '%s\n' "$*" >>"$FAKE_DASH0_LOG"
for name in DASH0_OTLP_URL DASH0_AUTH_TOKEN DASH0_DATASET; do
  [[ -n "${!name:-}" ]] || { echo "fake dash0: $name is not set" >&2; exit 1; }
done
[[ "${1:-} ${2:-}" == "logs send" ]] || { echo "fake dash0: unexpected call: $*" >&2; exit 1; }
if [[ -n "${FAKE_DASH0_REFUSE_SERVICE:-}" && " $* " == *" service.name=$FAKE_DASH0_REFUSE_SERVICE "* ]]; then
  echo "fake dash0: ingest refused" >&2
  exit 1
fi
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

begin() {  # begin <case>: a fresh log, the workflow's environment for the CLI, the fake's refusal off
  CURRENT="$1"
  CASE="$WORK/cases/$1"
  mkdir -p "$CASE"
  RUN_PATH="$BIN"
  export FAKE_DASH0_LOG="$CASE/dash0.args"
  export DASH0_OTLP_URL="https://ingress.dash0.invalid" DASH0_AUTH_TOKEN="$TOKEN" DASH0_DATASET="conferenceops"
  export GITHUB_SERVER_URL="https://github.example" GITHUB_REPOSITORY="acme/conferenceops-ai"
  unset FAKE_DASH0_REFUSE_SERVICE
  : >"$FAKE_DASH0_LOG"
}

run() {  # run <args>...: the script with only $RUN_PATH on PATH; sets RC, $CASE/stdout and $CASE/stderr
  RC=0
  (cd "$CASE" && PATH="$RUN_PATH" "$SCRIPT" "$@") >"$CASE/stdout" 2>"$CASE/stderr" || RC=$?
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

is_empty() {  # is_empty <file>
  if [[ -s "$1" ]]; then fail "${1##*/} is not empty: $(head -c 300 "$1")"; else pass; fi
}

line_count() {  # line_count <file> <n>
  local n
  n=$(wc -l <"$1" | tr -d ' ')
  if [[ "$n" == "$2" ]]; then pass; else fail "${1##*/} has $n lines, expected $2: $(head -c 600 "$1")"; fi
}

line_is() {  # line_is <file> <n> <the whole line>: line n of the file is exactly this
  local actual
  actual=$(sed -n "${2}p" "$1")
  if [[ "$actual" == "$3" ]]; then pass; else fail "${1##*/} line $2 is: $actual; expected: $3"; fi
}

marker() {  # marker <service> <status> <severity> <body>: the fake's line for the event the script must send
  echo "logs send $4 --event-name dash0.deployment --severity-number $3" \
    "--resource-attribute service.name=$1 --resource-attribute service.namespace=conferenceops" \
    "--resource-attribute service.version=$TAG --resource-attribute deployment.environment.name=aks" \
    "--log-attribute deployment.status=$2 --log-attribute vcs.repository.url.full=$REPOSITORY_URL" \
    "--log-attribute vcs.ref.head.revision=$TAG"
}

expect_markers() {  # expect_markers <status> <severity> <body>: exactly one event per service, in the chart's order
  local i
  line_count "$FAKE_DASH0_LOG" "${#SERVICES[@]}"
  for i in "${!SERVICES[@]}"; do
    line_is "$FAKE_DASH0_LOG" "$((i + 1))" "$(marker "${SERVICES[$i]}" "$1" "$2" "$3")"
  done
}

# --- Tests ----------------------------------------------------------------------------------------------------------

test_rejects_bad_arguments() {
  local name args
  for name in "no-arguments:" "no-tag:succeeded" "empty-tag:succeeded|" "unknown-status:success|$TAG" \
    "extra-argument:failed|$TAG|now"; do
    begin "${name%%:*}"
    args="${name#*:}"
    if [[ -z "$args" ]]; then run; else
      # shellcheck disable=SC2086  # the |-separated arguments split on purpose; an empty one must stay empty
      IFS='|' run $args
    fi
    expect_rc 1
    contains "$CASE/stderr" "usage: scripts/dash0-deploy-marker.sh <succeeded|failed> <tag>"
    is_empty "$FAKE_DASH0_LOG"
  done
}

test_needs_the_environment() {  # every variable the CLI or the event needs, each missing on its own
  local name
  for name in DASH0_OTLP_URL DASH0_AUTH_TOKEN DASH0_DATASET GITHUB_SERVER_URL GITHUB_REPOSITORY; do
    begin "no-$name"
    unset "$name"
    run succeeded "$TAG"
    expect_rc 1
    contains "$CASE/stderr" "$name is not set"
    is_empty "$FAKE_DASH0_LOG"
  done
}

test_needs_the_cli() {
  begin no-cli
  RUN_PATH="$BIN_NO_DASH0"
  run failed "$TAG"
  expect_rc 1
  contains "$CASE/stderr" "the dash0 CLI is not installed"
  is_empty "$FAKE_DASH0_LOG"
}

test_succeeded_marks_every_service() {
  begin succeeded
  run succeeded "$TAG"
  expect_rc 0
  expect_markers succeeded 9 "Deployed $TAG"
  contains "$CASE/stdout" \
    "Marked the deploy of $TAG as succeeded in Dash0 (dataset conferenceops) for web core-api discovery-api worker"
  is_empty "$CASE/stderr"
}

test_failed_marks_every_service() {
  begin failed
  run failed "$TAG"
  expect_rc 0
  expect_markers failed 17 "Deploy of $TAG failed"
  contains "$CASE/stdout" \
    "Marked the deploy of $TAG as failed in Dash0 (dataset conferenceops) for web core-api discovery-api worker"
  is_empty "$CASE/stderr"
}

test_fails_when_dash0_refuses_an_event() {  # a marker that didn't reach Dash0 fails the step, so the run says so
  begin refused
  export FAKE_DASH0_REFUSE_SERVICE=discovery-api
  run succeeded "$TAG"
  expect_rc 1
  line_count "$FAKE_DASH0_LOG" 3  # web, core-api, then the refused discovery-api event; the script stops there
  contains "$CASE/stderr" "fake dash0: ingest refused"
  lacks "$CASE/stdout" "Marked the deploy"
}

test_never_prints_the_token() {
  begin token
  run failed "$TAG"
  expect_rc 0
  lacks "$CASE/stdout" "$TOKEN"
  lacks "$CASE/stderr" "$TOKEN"
  lacks "$FAKE_DASH0_LOG" "$TOKEN"
}

main() {
  WORK=$(cd "$(mktemp -d "${TMPDIR:-/tmp}/dash0-deploy-marker-test.XXXXXX")" && pwd -P)
  trap 'rm -rf -- "$WORK"' EXIT
  write_fake
  BIN="$WORK/bin"
  BIN_NO_DASH0="$WORK/bin-no-dash0"
  make_bin "$BIN" bash dash0
  make_bin "$BIN_NO_DASH0" bash

  test_rejects_bad_arguments
  test_needs_the_environment
  test_needs_the_cli
  test_succeeded_marks_every_service
  test_failed_marks_every_service
  test_fails_when_dash0_refuses_an_event
  test_never_prints_the_token

  echo "dash0-deploy-marker tests: $PASSED checks passed, $FAILED failed."
  ((FAILED == 0))
}

main "$@"; exit
