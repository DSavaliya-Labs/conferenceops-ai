#!/usr/bin/env bash
# Tests for scripts/load-run.sh and for the summary of ops/load/search-peak.js. No cluster is used.
# - load-run.sh runs from a temporary copy of scripts/ and ops/load/, with a PATH that holds only the tools it needs,
#   a fake sleep that returns at once and a fake kubectl that keeps one Job's state in files. The fake hands the
#   steps that need no cluster (set env --local, patch --local, create configmap --dry-run=client) to the real
#   kubectl, with an empty kubeconfig.
# - search-peak.js runs in the real k6 against a local stub of discovery-api (python3), and its summary is compared
#   with the stub's own counts. These checks are skipped when k6, python3 or curl isn't installed.
#   Usage: bash scripts/tests/load-run.test.sh        Exits non-zero when a check fails.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd "$HERE/../.." && pwd -P)
SUMMARY_JSON='{"report":"conferenceops-load-summary"'
OLD_UID="5eed0001-aaaa-4bbb-8ccc-000000000001"  # an earlier run's Job
OLD8="5eed0001"
NEW8="a0000001"  # the first Job a case applies: the fake numbers them a0000001, a0000002, ...
WORK="" FIX="" CASE="" CURRENT="" RESULTS="" RC=0 PASSED=0 FAILED=0 SKIPPED=0 STUB_PID="" STUB_URL=""

# --- Fakes and fixtures ---------------------------------------------------------------------------------------------

write_fakes() {
  mkdir -p "$WORK/fakes"
  cat >"$WORK/fakes/kubectl" <<'EOF'
#!/usr/bin/env bash
# Fake kubectl for load-run.test.sh. One Job's state lives in files under $FAKE_KUBE/job, and the fake answers the
# calls scripts/load-run.sh makes. A Job the script applies plays out as k6 would run it: its pod stays unscheduled
# for FAKE_K6_PENDING_POLLS status polls, runs for one more, then k6 exits with FAKE_K6_EXIT (default 0; "none": no
# exit code). k6 runs with --quiet, so its summary reaches the log only when it stops, on its own or on SIGINT
# (exec ... kill -INT 1); FAKE_K6_SUMMARY=no leaves it out. Deleting a Job deletes its log with it.
# FAKE_KUBE_POLL_ERRORS=N fails the first N status polls, as an API outage would. The steps that need no cluster
# (set env --local, patch --local, create configmap --dry-run=client) run in the real kubectl.
set -euo pipefail
S="$FAKE_KUBE"
J="$FAKE_KUBE/job"
printf '%s\n' "$*" >>"$S/calls"

die() { printf '%s\n' "$*" >&2; exit 1; }
get() { if [[ -f "$J/$1" ]]; then cat "$J/$1"; fi; }
put() { printf '%s' "$2" >"$J/$1"; }
real() { KUBECONFIG="$S/empty-kubeconfig" "$FAKE_KUBECTL_REAL" "$@"; }  # a step that needs no cluster

k6_stops() {  # k6_stops <exit code | none>: k6 prints its summary as it stops, and the Job finishes
  if [[ "$1" == none ]]; then put exit ""; put phase failed; return 0; fi
  if [[ "${FAKE_K6_SUMMARY:-yes}" != no ]]; then cat "$FAKE_K6_SUMMARY_FILE" >>"$J/log"; fi
  put exit "$1"
  if [[ "$1" == 0 ]]; then put phase complete; else put phase failed; fi
}

advance() {  # a status poll: a Job the script applied goes from unscheduled to running to finished
  local polls phase
  if [[ "$(get applied)" != yes ]]; then return 0; fi
  polls=$(get polls)
  polls=$((${polls:-0} + 1))
  put polls "$polls"
  phase=$(get phase)
  if [[ "$phase" == complete || "$phase" == failed ]]; then return 0; fi
  if ((polls <= ${FAKE_K6_PENDING_POLLS:-0})); then
    put phase pending
  elif [[ "$phase" != running ]]; then
    put phase running
  else
    k6_stops "${FAKE_K6_EXIT:-0}"
  fi
}

render() {  # render <jsonpath template>: fills in the expressions load-run.sh uses, from the Job's state
  local t="$1" uid phase exit_code active="" conditions="" started="" reason="" message="" ready="" i
  uid=$(get uid)
  phase=$(get phase)
  exit_code=$(get exit)
  case "$phase" in
    created) active=1 ;;
    pending) active=1 reason=Unschedulable message="0/4 nodes are available: 4 Insufficient cpu." ;;
    running) active=1 started=2026-11-09T19:00:00Z ;;
    complete) conditions="SuccessCriteriaMet Complete " ;;
    failed) conditions="FailureTarget Failed " ;;
  esac
  for ((i = 0; i < $(cat "$S/ready"); i++)); do ready="${ready}true"$'\n'; done
  t=${t//'{.metadata.uid}'/$uid}
  t=${t//'{.status.active}'/$active}
  t=${t//'{range .status.conditions[?(@.status=="True")]}{.type} {end}'/$conditions}
  t=${t//'{.items[0].status.containerStatuses[0].state.running.startedAt}'/$started}
  t=${t//'{.items[0].status.containerStatuses[0].state.waiting.reason}'/}
  t=${t//'{.items[0].status.containerStatuses[0].state.terminated.exitCode}'/$exit_code}
  t=${t//'{.items[0].status.conditions[?(@.type=="PodScheduled")].reason}'/$reason}
  t=${t//'{.items[0].status.conditions[?(@.type=="PodScheduled")].message}'/$message}
  t=${t//'{range .items[?(@.addressType=="IPv4")].endpoints[*]}{.conditions.ready}{"\n"}{end}'/$ready}
  [[ "$t" != *'{'* ]] || die "fake kubectl: a jsonpath expression it doesn't know: $1"
  printf '%s' "$t"
}

if [[ "$*" == "config current-context" ]]; then
  [[ -s "$S/context" ]] || die "error: current-context is not set"
  cat "$S/context"
  exit 0
fi
if [[ "${1:-}" == set || "${1:-}" == patch ]]; then
  [[ " $* " == *" --local "* ]] || die "fake kubectl: $1 without --local would change the cluster"
  real "$@"
  exit
fi
[[ "${1:-}" == --context && "${3:-}" == --namespace && "${2:-}" == "$(cat "$S/context")" ]] \
  || die "fake kubectl: not pinned to the current context and a namespace: $*"
namespace="$4"
shift 4
template="" selector="" previous=""
for arg in "$@"; do
  case "$arg" in jsonpath=*) template="${arg#jsonpath=}" ;; esac
  if [[ "$previous" == -l ]]; then selector="$arg"; fi
  previous="$arg"
done

case "$1 ${2:-}" in
  "get namespace")
    [[ ! -e "$S/no-namespace" ]] || die "Error from server (NotFound): namespaces \"$3\" not found"
    echo "namespace/$3"
    ;;
  "get service")
    [[ ! -e "$S/no-service" ]] || die "Error from server (NotFound): services \"$3\" not found"
    echo "service/$3"
    ;;
  "get endpointslices") render "$template" ;;
  "get job")
    if [[ ! -f "$J/uid" ]]; then
      [[ " $* " == *" --ignore-not-found "* ]] || die "Error from server (NotFound): jobs.batch \"$3\" not found"
      exit 0
    fi
    if [[ "$template" == *.status.conditions* ]]; then
      errors=$(cat "$S/poll-errors" 2>/dev/null || echo 0)
      if ((errors < ${FAKE_KUBE_POLL_ERRORS:-0})); then
        echo $((errors + 1)) >"$S/poll-errors"
        die "Unable to connect to the server: net/http: TLS handshake timeout"
      fi
      advance
    fi
    render "$template"
    ;;
  "get pods")
    [[ -f "$J/uid" && "$selector" == "batch.kubernetes.io/controller-uid=$(get uid)" ]] \
      || die "error: error executing jsonpath: array index out of bounds: index 0, length 0"
    render "$template"
    ;;
  "logs job/"*)
    [[ -f "$J/uid" ]] || die "Error from server (NotFound): jobs.batch \"conferenceops-load\" not found"
    if [[ "$(get phase)" == pending ]]; then exit 0; fi  # as the real kubectl does for a pod that has no node yet
    [[ -f "$J/log" ]] \
      || die "error: container \"k6\" in pod \"conferenceops-load-x7k2p\" is waiting to start: ContainerCreating"
    cat "$J/log"
    ;;
  "exec job/"*)
    [[ "$*" == "exec job/conferenceops-load -- kill -INT 1" ]] || die "fake kubectl: unexpected exec: $*"
    [[ -f "$J/uid" && "$(get phase)" == running ]] \
      || die "error: unable to upgrade connection: container not found (\"k6\")"
    k6_stops 105
    ;;
  "create configmap")
    [[ " $* " == *" --dry-run=client "* ]] || die "fake kubectl: create configmap without --dry-run=client"
    real --namespace "$namespace" "$@" -o json >"$S/configmap.json"
    real --namespace "$namespace" "$@"
    ;;
  "apply -f")
    input=$(cat)
    if [[ "$input" == *"kind: ConfigMap"* ]]; then
      echo "configmap/conferenceops-load configured"
      exit 0
    fi
    [[ "$(jq -r .kind <<<"$input")" == Job ]] || die "fake kubectl: apply got neither the ConfigMap nor a Job"
    [[ ! -f "$J/uid" ]] || die "The Job \"conferenceops-load\" is invalid: spec.template: field is immutable"
    n=$(($(cat "$S/uids" 2>/dev/null || echo 0) + 1))
    echo "$n" >"$S/uids"
    mkdir -p "$J"
    put uid "$(printf 'a%07d-1111-4222-8333-%012d' "$n" "$n")"
    put phase created
    put applied yes
    put exit ""
    : >"$J/log"
    printf '%s\n' "$input" >"$S/applied.json"
    echo "job.batch/conferenceops-load created"
    ;;
  "delete job")
    if [[ -f "$J/uid" ]]; then
      printf '%s %s\n' "$(get uid)" "$(get phase)" >>"$S/deleted"
      rm -rf "$J"
      echo "job.batch \"$3\" deleted"
    fi
    ;;
  *) die "fake kubectl: unexpected call: $*" ;;
esac
EOF
  cat >"$WORK/fakes/sleep" <<'EOF'
#!/usr/bin/env bash
# Fake sleep: returns at once, so the script's polling runs fast. After 20 calls in one run it fails, so a wait that
# never ends fails the test instead of spinning.
n=$(($(cat "$FAKE_KUBE/sleeps" 2>/dev/null || echo 0) + 1))
echo "$n" >"$FAKE_KUBE/sleeps"
if ((n > 20)); then echo "fake sleep: called $n times; is a wait stuck?" >&2; exit 1; fi
EOF
  cat >"$WORK/fakes/discovery_stub.py" <<'EOF'
"""Stub of discovery-api's POST /api/v1/search for load-run.test.sh. It answers by arrival order, from a fixed cycle,
counts what it sent, and returns the counts at GET /counts. The answers are synthetic test inputs.
  Usage: python3 -I discovery_stub.py <mixed | capacity> <port-file>"""
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE, PORT_FILE = sys.argv[1], sys.argv[2]
# In mixed mode, the first request outlasts k6's 10 s timeout, and the rest follow this cycle.
CYCLE = ["hybrid", "lexical", "capacity", "unavailable", "hybrid", "error", "no-results", "not-json", "drop", "teapot"]
ANSWERS = {
    "hybrid": (200, {"results": [{"sessionId": "s-1"}], "retrievalMode": "hybrid"}),
    "lexical": (200, {"results": [], "retrievalMode": "lexical"}),
    "no-results": (200, {"retrievalMode": "hybrid"}),
    "capacity": (503, {"error": {"code": "DISCOVERY_CAPACITY_EXHAUSTED", "message": "Try again shortly."}}),
    "unavailable": (503, {"error": {"code": "SEARCH_UNAVAILABLE", "message": "Elasticsearch is unavailable."}}),
    "error": (500, {"error": {"code": "INTERNAL", "message": "Unexpected error."}}),
    "teapot": (418, {"error": {"code": "TEAPOT", "message": "Not a tracked status."}}),
}
lock = threading.Lock()
counts = {"requests": 0, "post_connections": 0, "load_test_agent": 0, "bad_body": 0}


def kind_of(n):
    if MODE == "capacity":
        return "capacity"
    return "stall" if n == 1 else CYCLE[(n - 2) % len(CYCLE)]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        with lock:
            body = json.dumps(counts).encode()
        self.reply(200, body, "application/json")

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            query = json.loads(raw)["query"]
            good = isinstance(query, str) and query.strip() != ""
        except (ValueError, KeyError, TypeError):
            good = False
        with lock:
            counts["requests"] += 1
            kind = kind_of(counts["requests"])
            counts[kind] = counts.get(kind, 0) + 1
            if not getattr(self, "posted", False):  # the first POST on this connection
                counts["post_connections"] += 1
                self.posted = True
            counts["load_test_agent"] += self.headers.get("User-Agent") == "conferenceops-load/search-peak (k6)"
            counts["bad_body"] += not good
        if kind in ("stall", "drop"):  # no answer: k6 times out (stall) or sees the connection close (drop)
            if kind == "stall":
                time.sleep(12)
            self.close_connection = True
            return
        if kind == "not-json":
            self.reply(200, b"<html>search</html>", "text/html")
            return
        status, payload = ANSWERS[kind]
        self.reply(status, json.dumps(payload).encode(), "application/json")

    def reply(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
server.daemon_threads = True
with open(PORT_FILE + ".tmp", "w") as f:
    f.write(str(server.server_address[1]))
os.replace(PORT_FILE + ".tmp", PORT_FILE)
server.serve_forever()
EOF
  chmod +x "$WORK/fakes/kubectl" "$WORK/fakes/sleep"
}

write_fixtures() {
  mkdir -p "$FIX"
  # What k6 writes while it runs with --quiet: warnings on stderr, never the summary.
  cat >"$FIX/k6-running.txt" <<'EOF'
time="2026-11-09T19:00:05Z" level=warning msg="Request Failed" error="request timeout"
EOF
  : >"$FIX/empty.txt"
  # The end of what search-peak.js prints when k6 stops (shortened): the readable block, then the JSON line.
  cat >"$FIX/k6-summary.txt" <<'EOF'

== ConferenceOps search capacity test (ops/load/search-peak.js) ==
Sent                2400
http_req_failed     0.00%  (0 of 2400 sent)

{"report":"conferenceops-load-summary","test":"search-peak","sent":2400,"failed":0}
EOF
}

# A PATH directory that holds only the given tools: real ones, or the fakes for kubectl and sleep.
make_bin() {  # make_bin <dir> <tool>...
  local dir="$1" tool
  shift
  mkdir -p "$dir"
  for tool in "$@"; do
    case "$tool" in
      kubectl | sleep) cp "$WORK/fakes/$tool" "$dir/$tool" ;;
      bash) ln -s "$BASH" "$dir/bash" ;;
      *) ln -s "$(command -v "$tool")" "$dir/$tool" ;;
    esac
  done
}

# --- Harness --------------------------------------------------------------------------------------------------------

begin() {  # begin <case>: a fresh copy of the load files, and a cluster with the app deployed and no Job
  CURRENT="$1"
  CASE="$WORK/cases/$1"
  mkdir -p "$CASE/repo/scripts" "$CASE/repo/ops/load" "$CASE/kube"
  cp "$REPO_ROOT/scripts/load-run.sh" "$CASE/repo/scripts/"
  cp "$REPO_ROOT/ops/load/k6-job.yaml" "$REPO_ROOT/ops/load/search-peak.js" "$REPO_ROOT/ops/load/search-summary.js" \
    "$REPO_ROOT/ops/load/queries.json" "$CASE/repo/ops/load/"
  chmod +x "$CASE/repo/scripts/load-run.sh"
  RESULTS="$CASE/repo/ops/load/results"
  export FAKE_KUBE="$CASE/kube"
  echo conferenceops-aks >"$FAKE_KUBE/context"
  echo 2 >"$FAKE_KUBE/ready"
  : >"$FAKE_KUBE/calls"
  : >"$FAKE_KUBE/deleted"
  : >"$FAKE_KUBE/empty-kubeconfig"
  unset FAKE_K6_EXIT FAKE_K6_SUMMARY FAKE_K6_PENDING_POLLS FAKE_KUBE_POLL_ERRORS
}

seed_job() {  # seed_job <uid> <phase> [<log file>]: an earlier run's Job: running, pending, complete or failed
  local j="$FAKE_KUBE/job"
  mkdir -p "$j"
  printf '%s' "$1" >"$j/uid"
  printf '%s' "$2" >"$j/phase"
  case "$2" in
    complete) printf 0 >"$j/exit" ;;
    failed) printf 137 >"$j/exit" ;;  # for example OOMKilled: k6 never got to print its summary
    *) : >"$j/exit" ;;
  esac
  if (($# >= 3)); then cp "$3" "$j/log"; fi
}

run() {  # run <bin dir> <args>...: the copy of load-run.sh with only that PATH; sets RC, $CASE/stdout, $CASE/stderr
  local bin="$1"
  shift
  RC=0
  rm -f "$FAKE_KUBE/sleeps"
  (cd "$CASE" && PATH="$bin" "$CASE/repo/scripts/load-run.sh" "$@") >"$CASE/stdout" 2>"$CASE/stderr" || RC=$?
}

pass() { PASSED=$((PASSED + 1)); }

fail() {
  FAILED=$((FAILED + 1))
  printf 'FAIL [%s] %s\n' "$CURRENT" "$*" >&2
}

skip() {
  SKIPPED=$((SKIPPED + 1))
  printf 'SKIP [%s] %s\n' "$CURRENT" "$*" >&2
}

expect_rc() {
  if [[ "$RC" == "$1" ]]; then pass; else fail "exit $RC, expected $1. stderr: $(head -c 800 "$CASE/stderr")"; fi
}

contains() {  # contains <file> <text>
  if grep -qF -- "$2" "$1"; then pass; else fail "${1##*/} lacks: $2"; fi
}

lacks() {  # lacks <file> <text>
  if grep -qF -- "$2" "$1"; then fail "${1##*/} contains: $2"; else pass; fi
}

count_is() {  # count_is <file> <text> <n>: exactly n lines hold the text
  local n
  n=$(grep -cF -- "$2" "$1" || true)
  if [[ "$n" == "$3" ]]; then pass; else fail "${1##*/} has $n lines with '$2', expected $3"; fi
}

line_count() {  # line_count <file> <n>
  local n
  n=$(wc -l <"$1" | tr -d ' ')
  if [[ "$n" == "$2" ]]; then pass; else fail "${1##*/} has $n lines, expected $2: $(head -c 400 "$1")"; fi
}

called() { contains "$FAKE_KUBE/calls" "$1"; }
not_called() { lacks "$FAKE_KUBE/calls" "$1"; }

calls_in_order() {  # calls_in_order <text>...: the fake kubectl got calls holding these texts, in this order
  local line i=1
  local -a want=("$@")
  while IFS= read -r line; do
    if ((i <= ${#want[@]})) && [[ "$line" == *"${want[i - 1]}"* ]]; then i=$((i + 1)); fi
  done <"$FAKE_KUBE/calls"
  if ((i > ${#want[@]})); then pass; else fail "kubectl calls aren't in this order: $*"; fi
}

job_is() {  # job_is <phase | none>: the cluster's Job
  local got=none
  if [[ -f "$FAKE_KUBE/job/uid" ]]; then got=$(cat "$FAKE_KUBE/job/phase"); fi
  if [[ "$got" == "$1" ]]; then pass; else fail "the Job is $got, expected $1"; fi
}

results_count() {  # results_count <glob> <n>: files in ops/load/results/ that match
  local n=0 f
  for f in "$RESULTS"/$1; do
    if [[ -e "$f" ]]; then n=$((n + 1)); fi
  done
  if [[ "$n" == "$2" ]]; then pass; else fail "$n files match results/$1, expected $2: $(ls "$RESULTS" 2>&1)"; fi
}

jq_true() {  # jq_true <file> [jq options] <filter>: the filter is true for the file
  local file="$1"
  shift
  if jq -e "$@" "$file" >/dev/null 2>&1; then pass; else fail "${file##*/}: not true: $*"; fi
}

# --- Tests: scripts/load-run.sh -------------------------------------------------------------------------------------

test_rejects_bad_arguments() {
  begin bad-arguments
  local args
  for args in "--rate 0" "--rate 2.5" "--rate abc" "--rate=-3" "--rate" "--duration 5min" "--duration 0s" \
    "--duration 0m" "--duration 1.5m" "--duration 1s1m" "--duration 30m1h" "--duration -5m" "--duration=" "--bogus"; do
    # shellcheck disable=SC2086 # each case is a list of words
    run "$BIN" $args
    if [[ "$RC" == 2 ]]; then pass; else fail "'$args' exited $RC, expected 2"; fi
  done
  contains "$CASE/stderr" "unknown argument: --bogus"
  run "$BIN" --duration "5 minutes"
  expect_rc 2
  contains "$CASE/stderr" "--duration must look like 90s, 5m or 1h30m, got '5 minutes'"
  run "$BIN" --duration ""
  expect_rc 2
  contains "$CASE/stderr" "--duration must look like 90s, 5m or 1h30m, got ''"
  line_count "$FAKE_KUBE/calls" 0  # nothing reached kubectl, not even the context
  run "$BIN" --help
  expect_rc 0
  contains "$CASE/stdout" "Usage: scripts/load-run.sh [--rate N] [--duration 5m] [--replace] [--context-ok]"
}

# Regression test: kubectl set env --local prints nothing when the file already has the values, and k6-job.yaml's own
# values are the defaults (8 requests/s for 5m), so the first command in the README stopped before it started.
test_runs_with_the_job_files_own_values() {
  begin file-values
  run "$BIN" --rate 8 --duration 5m
  expect_rc 0
  contains "$CASE/stderr" "Job conferenceops-load: 8 requests/s for 5m (300 s)"
  jq_true "$FAKE_KUBE/applied.json" '[.spec.template.spec.containers[0].env[] | {(.name): .value}] | add
    == {"BASE_URL": "http://conferenceops-discovery", "RATE": "8", "DURATION": "5m"}'
  results_count "search-peak-*-8rps-$NEW8.log" 1
}

test_reads_durations() {
  begin durations
  local pair
  for pair in "90s 90" "5m 300" "1h30m 5400" "2m30s 150" "08m 480" "0003m 180" "1h 3600"; do
    run "$BIN" --duration "${pair% *}"
    expect_rc 0
    contains "$CASE/stderr" "Job conferenceops-load: 8 requests/s for ${pair% *} (${pair#* } s)"
  done
}

test_applies_the_rate_and_duration() {
  begin applies
  run "$BIN" --rate=12 --duration=90s
  expect_rc 0
  contains "$CASE/stderr" "target: Service conferenceops-discovery with 2 Ready endpoint(s)"
  jq_true "$FAKE_KUBE/applied.json" '.kind == "Job" and .metadata.name == "conferenceops-load"
    and .metadata.namespace == "conferenceops"'
  jq_true "$FAKE_KUBE/applied.json" '[.spec.template.spec.containers[0].env[] | {(.name): .value}] | add
    == {"BASE_URL": "http://conferenceops-discovery", "RATE": "12", "DURATION": "90s"}'
  # shellcheck disable=SC2016 # $js, $sum and $q are jq's variables
  jq_true "$FAKE_KUBE/configmap.json" --rawfile js "$CASE/repo/ops/load/search-peak.js" \
    --rawfile sum "$CASE/repo/ops/load/search-summary.js" --rawfile q "$CASE/repo/ops/load/queries.json" \
    '.metadata.name == "conferenceops-load"
     and .data == {"search-peak.js": $js, "search-summary.js": $sum, "queries.json": $q}'
  calls_in_order "create configmap conferenceops-load" "apply -f -" "delete job conferenceops-load" "apply -f -" \
    "get job conferenceops-load -o jsonpath={.metadata.uid}"
  contains "$CASE/stdout" "$SUMMARY_JSON"  # the logs are printed
  results_count "search-peak-*-12rps-$NEW8.log" 1
}

test_refuses_a_foreign_context() {
  begin foreign-context
  echo aks-prod-east >"$FAKE_KUBE/context"
  run "$BIN"
  expect_rc 1
  contains "$CASE/stderr" "refusing to load kubectl context 'aks-prod-east'"
  line_count "$FAKE_KUBE/calls" 1
  called "config current-context"
  run "$BIN" --context-ok
  expect_rc 0
  called "--context aks-prod-east --namespace conferenceops apply -f -"
}

test_stops_when_the_app_isnt_deployed() {
  begin not-deployed
  touch "$FAKE_KUBE/no-namespace"
  run "$BIN"
  expect_rc 1
  contains "$CASE/stderr" "can't read namespace conferenceops"
  rm "$FAKE_KUBE/no-namespace"
  touch "$FAKE_KUBE/no-service"
  run "$BIN"
  expect_rc 1
  contains "$CASE/stderr" "Service conferenceops-discovery doesn't exist in namespace conferenceops"
  not_called "apply"
  rm "$FAKE_KUBE/no-service"
  echo 0 >"$FAKE_KUBE/ready"
  run "$BIN"
  expect_rc 0
  contains "$CASE/stderr" "warning: no Ready endpoints, so every request will fail"
}

test_needs_jq() {
  begin no-jq
  run "$BIN_NO_JQ"
  expect_rc 1
  contains "$CASE/stderr" "needs kubectl and jq"
  line_count "$FAKE_KUBE/calls" 0
}

test_passes_k6_exit_codes() {
  begin exit-codes
  local pair
  for pair in "0 every threshold met" "99 a threshold was crossed" "105 stopped before the end of its duration"; do
    export FAKE_K6_EXIT="${pair%% *}"
    run "$BIN"
    expect_rc "${pair%% *}"
    contains "$CASE/stderr" "k6 exited ${pair%% *}, ${pair#* }"
    contains "$CASE/stdout" "$SUMMARY_JSON"
  done
  export FAKE_K6_EXIT=none
  run "$BIN"
  expect_rc 1
  contains "$CASE/stderr" "the k6 container has no exit code"
  results_count "search-peak-*-8rps-*.log" 4
}

test_refuses_while_a_run_is_active() {
  begin active
  seed_job "$OLD_UID" running "$FIX/k6-running.txt"
  run "$BIN"
  expect_rc 1
  contains "$CASE/stderr" "a load test is still running (Job conferenceops-load)"
  contains "$CASE/stderr" "pass --replace to stop it and start this one"
  not_called "exec"
  not_called "logs"
  not_called "delete"
  not_called "apply"
  job_is running
  results_count "*" 0
  seed_job "$OLD_UID" pending  # a pod that waits for a node is active too
  run "$BIN"
  expect_rc 1
  job_is pending
}

# The regression test for --replace: k6 prints its summary only when it stops, so the copy must come after SIGINT.
test_replace_saves_the_stopped_runs_summary() {
  begin replace
  seed_job "$OLD_UID" running "$FIX/k6-running.txt"
  run "$BIN" --replace
  expect_rc 0
  local saved="$RESULTS/search-peak-previous-$OLD8.log"
  if [[ -f "$saved" ]]; then pass; else fail "no copy of the stopped run's logs"; fi
  contains "$saved" "$SUMMARY_JSON"
  contains "$saved" 'level=warning msg="Request Failed"'
  contains "$CASE/stderr" "--replace: stopping the running load test with SIGINT"
  contains "$CASE/stderr" "saved the previous run's logs to ops/load/results/search-peak-previous-$OLD8.log"
  calls_in_order "exec job/conferenceops-load -- kill -INT 1" "logs job/conferenceops-load" \
    "delete job conferenceops-load" "apply -f -"
  contains "$FAKE_KUBE/deleted" "$OLD_UID failed"  # k6 had stopped (exit 105) before its Job was deleted
  results_count "*" 2
}

test_replace_when_k6_never_started() {
  begin replace-unscheduled
  seed_job "$OLD_UID" pending  # its container never started, so there are no logs and no summary
  run "$BIN" --replace
  expect_rc 0
  contains "$CASE/stderr" "warning: couldn't send SIGINT to k6 (kubectl's error is above)"
  contains "$CASE/stderr" "warning: the previous run's logs are empty (k6 may never have started);"
  contains "$CASE/stderr" "--replace deletes Job conferenceops-load anyway"
  lacks "$CASE/stderr" "saved the previous run's logs"
  results_count "*-$OLD8.log" 0  # no empty copy is left behind
  contains "$FAKE_KUBE/deleted" "$OLD_UID pending"
}

test_saves_a_previous_runs_logs_once() {
  begin saved-once
  seed_job "$OLD_UID" complete "$FIX/k6-summary.txt"  # its waiter was stopped with Ctrl-C, so nobody saved it
  run "$BIN"
  expect_rc 0
  contains "$CASE/stderr" "saved the previous run's logs to ops/load/results/search-peak-previous-$OLD8.log"
  contains "$RESULTS/search-peak-previous-$OLD8.log" "$SUMMARY_JSON"
  results_count "*-$NEW8.log" 1  # this run's own copy, saved by its waiter
  run "$BIN"
  expect_rc 0
  lacks "$CASE/stderr" "saved the previous run's logs"
  results_count "*-$OLD8.log" 1
  results_count "*-$NEW8.log" 1
  results_count "*.log" 3
}

test_keeps_a_previous_job_without_a_summary() {
  begin no-summary
  seed_job "$OLD_UID" failed "$FIX/k6-running.txt"
  run "$BIN"
  expect_rc 1
  contains "$CASE/stderr" "the previous run's logs have no k6 summary"
  contains "$CASE/stderr" "(what they hold is in ops/load/results/search-peak-previous-$OLD8.log), so Job"
  contains "$CASE/stderr" "so Job conferenceops-load is left in place."
  contains "$CASE/stderr" "then pass --replace to delete it."
  not_called "delete"
  not_called "apply"
  job_is failed
  run "$BIN"  # trying again doesn't delete it either
  expect_rc 1
  job_is failed
  results_count "*-$OLD8.log" 1
  run "$BIN" --replace
  expect_rc 0
  contains "$CASE/stderr" "warning: the previous run's logs have no k6 summary"
  contains "$CASE/stderr" "--replace deletes Job conferenceops-load anyway"
  not_called "exec"  # it had finished, so there was nothing to stop
  contains "$FAKE_KUBE/deleted" "$OLD_UID failed"
}

test_keeps_a_previous_job_whose_logs_cant_be_read() {
  begin logs-unreadable
  seed_job "$OLD_UID" complete  # no log: kubectl logs fails
  run "$BIN"
  expect_rc 1
  contains "$CASE/stderr" "couldn't copy the previous run's logs (kubectl's error is above), so Job"
  contains "$CASE/stderr" "so Job conferenceops-load is left in place."
  not_called "delete"
  results_count "*" 0
  run "$BIN" --replace
  expect_rc 0
  contains "$CASE/stderr" "--replace deletes Job conferenceops-load anyway"
}

test_keeps_a_previous_job_with_empty_logs() {
  begin logs-empty
  seed_job "$OLD_UID" failed "$FIX/empty.txt"  # k6 never printed anything
  run "$BIN"
  expect_rc 1
  contains "$CASE/stderr" "the previous run's logs are empty (k6 may never have started), so Job"
  contains "$CASE/stderr" "so Job conferenceops-load is left in place."
  not_called "delete"
  results_count "*" 0  # no empty copy is left behind
}

test_reports_an_unschedulable_pod() {
  begin unschedulable
  export FAKE_K6_PENDING_POLLS=3
  run "$BIN"
  expect_rc 0
  count_is "$CASE/stderr" \
    "the k6 pod isn't scheduled: Unschedulable (0/4 nodes are available: 4 Insufficient cpu.); see kubectl describe" 1
  count_is "$CASE/stderr" "k6 running since 2026-11-09T19:00:00Z" 1
}

test_waits_through_short_api_outages() {
  begin api-hiccup
  export FAKE_KUBE_POLL_ERRORS=5
  run "$BIN"
  expect_rc 0
  begin api-down
  export FAKE_KUBE_POLL_ERRORS=6
  run "$BIN"
  expect_rc 1
  contains "$CASE/stderr" "can't read Job conferenceops-load; stopped waiting, but the test may still be running"
}

# --- Tests: ops/load/search-peak.js in k6, against a stub of discovery-api ------------------------------------------

have_k6() { command -v k6 >/dev/null && command -v python3 >/dev/null && command -v curl >/dev/null; }

start_stub() {  # start_stub <mixed | capacity>: the stub in the background; sets STUB_PID and STUB_URL
  local i
  python3 -I "$WORK/fakes/discovery_stub.py" "$1" "$CASE/port" 2>"$CASE/stub.err" &
  STUB_PID=$!
  for ((i = 0; i < 100; i++)); do
    if [[ -s "$CASE/port" ]]; then break; fi
    sleep 0.1
  done
  if [[ ! -s "$CASE/port" ]]; then
    fail "the stub didn't start: $(cat "$CASE/stub.err")"
    return 1
  fi
  STUB_URL="http://127.0.0.1:$(cat "$CASE/port")"
}

stop_stub() {
  if [[ -n "$STUB_PID" ]]; then
    kill "$STUB_PID" 2>/dev/null || true
    wait "$STUB_PID" 2>/dev/null || true
    STUB_PID=""
  fi
}

run_k6() {  # run_k6 <rate> <duration>: the copy of search-peak.js against the stub; sets RC, and the stub's counts
  RC=0
  k6 run --quiet --no-usage-report -e "BASE_URL=$STUB_URL" -e "RATE=$1" -e "DURATION=$2" \
    "$CASE/repo/ops/load/search-peak.js" >"$CASE/k6.out" 2>"$CASE/k6.err" || RC=$?
  grep -F -- "$SUMMARY_JSON" "$CASE/k6.out" >"$CASE/report.json" || true
  curl -sS "$STUB_URL/counts" >"$CASE/counts.json" || fail "couldn't read the stub's counts"
  stop_stub
}

report_is() {  # report_is <jq filter over the summary's JSON line> <expected JSON>
  local got
  got=$(jq -c "$1" "$CASE/report.json" 2>&1) || got="unreadable: $got"
  if [[ "$got" == "$2" ]]; then pass; else fail "report $1 is $got, expected $2"; fi
}

matches() {  # matches <jq filter over the summary> <jq filter over the stub's counts>: the two agree
  local got want
  got=$(jq -c "$1" "$CASE/report.json" 2>&1) || got="unreadable: $got"
  want=$(jq -c "$2" "$CASE/counts.json" 2>&1) || want="unreadable: $want"
  if [[ "$got" == "$want" ]]; then pass; else fail "report $1 is $got, but the stub's $2 is $want"; fi
}

test_summary_matches_what_the_stub_sent() {
  begin k6-summary
  if ! have_k6; then skip "k6, python3 or curl isn't installed"; return 0; fi
  start_stub mixed || return 0
  run_k6 10 3s
  expect_rc 99  # http_req_failed is crossed, and a crossed threshold doesn't abort the run
  local ok200='(.hybrid // 0) + (.lexical // 0) + (.["no-results"] // 0) + (.["not-json"] // 0)'
  jq_true "$CASE/counts.json" '[.hybrid, .lexical, .capacity, .unavailable, .error, .["no-results"], .["not-json"],
    .drop, .teapot, .stall] | all(. != null and . > 0)'  # every kind of answer was sent at least once
  matches '.sent' '.requests'
  matches '.scheduled' '.requests'
  report_is '.droppedIterations' '0'
  matches '.status | keys' '["200", "500", "503", "other"]'
  matches '.status["200"]' "$ok200"
  matches '.status["503"]' '.capacity + .unavailable'
  matches '.status["500"]' '.error'
  matches '.status.other' '.teapot'
  matches '.rejected503.count' '.capacity + .unavailable'
  matches '.rejected503.capacityExhausted' '.capacity'
  matches '.noResponse.count' '.stall + .drop'
  report_is '.noResponse.timedOut' '1'
  matches '.retrievalMode' '{hybrid: .hybrid, lexical: .lexical, unknown: (.["no-results"] + .["not-json"])}'
  matches '.failed' ".requests - ($ok200)"
  report_is '[.thresholds[] | [.metric, .result]]' \
    '[["dropped_iterations","pass"],["http_req_duration{expected_response:true}","pass"],["http_req_failed","fail"]]'
  # What the stub saw: one request per connection, labeled as load-test traffic, with a query from the corpus.
  matches '.sent' '.post_connections'
  matches '.sent' '.load_test_agent'
  jq_true "$CASE/counts.json" '.bad_body == 0'
  contains "$CASE/k6.out" "== ConferenceOps search capacity test (ops/load/search-peak.js) =="
  contains "$CASE/k6.out" "  FAIL     http_req_failed  rate<0.01"
  # PRIV-1: no query text reaches the summary or k6's own output.
  local query leaked=0
  while IFS= read -r query; do
    if grep -qF -- "$query" "$CASE/k6.out" "$CASE/k6.err"; then leaked=$((leaked + 1)); fi
  done < <(jq -r '.[]' "$CASE/repo/ops/load/queries.json")
  if ((leaked == 0)); then pass; else fail "$leaked queries appear in k6's output"; fi
}

test_summary_reports_no_data() {
  begin k6-no-data
  if ! have_k6; then skip "k6, python3 or curl isn't installed"; return 0; fi
  start_stub capacity || return 0  # every search is turned away, so no request succeeds
  run_k6 5 2s
  expect_rc 99
  matches '.status' '{"503": .requests}'
  matches '.rejected503.capacityExhausted' '.requests'
  report_is '.p95Ms.successful' 'null'
  # k6 passes a threshold whose metric has no samples; the summary says NO DATA instead.
  report_is '[.thresholds[] | [.metric, .result]]' \
    '[["dropped_iterations","pass"],["http_req_duration{expected_response:true}","no data"],["http_req_failed","fail"]]'
  contains "$CASE/k6.out" "  NO DATA  http_req_duration{expected_response:true}  p(95)<1500"
}

main() {
  local real_kubectl
  command -v jq >/dev/null || { echo "These tests need jq." >&2; return 1; }
  if ! real_kubectl=$(command -v kubectl); then
    echo "These tests need kubectl, which they use offline only (--local and --dry-run=client)." >&2
    return 1
  fi
  WORK=$(cd "$(mktemp -d "${TMPDIR:-/tmp}/load-run-test.XXXXXX")" && pwd -P)
  trap 'stop_stub; rm -rf -- "$WORK"' EXIT
  FIX="$WORK/fixtures"
  export FAKE_KUBECTL_REAL="$real_kubectl" FAKE_K6_SUMMARY_FILE="$FIX/k6-summary.txt"
  write_fakes
  write_fixtures
  local tools=(bash cat date dirname grep mkdir rm)
  BIN="$WORK/bin/all" BIN_NO_JQ="$WORK/bin/no-jq"
  make_bin "$BIN" "${tools[@]}" jq kubectl sleep
  make_bin "$BIN_NO_JQ" "${tools[@]}" kubectl sleep

  test_rejects_bad_arguments
  test_runs_with_the_job_files_own_values
  test_reads_durations
  test_applies_the_rate_and_duration
  test_refuses_a_foreign_context
  test_stops_when_the_app_isnt_deployed
  test_needs_jq
  test_passes_k6_exit_codes
  test_refuses_while_a_run_is_active
  test_replace_saves_the_stopped_runs_summary
  test_replace_when_k6_never_started
  test_saves_a_previous_runs_logs_once
  test_keeps_a_previous_job_without_a_summary
  test_keeps_a_previous_job_whose_logs_cant_be_read
  test_keeps_a_previous_job_with_empty_logs
  test_reports_an_unschedulable_pod
  test_waits_through_short_api_outages
  test_summary_matches_what_the_stub_sent
  test_summary_reports_no_data

  echo "load-run tests: $PASSED checks passed, $FAILED failed, $SKIPPED skipped."
  ((FAILED == 0))
}

main "$@"; exit
