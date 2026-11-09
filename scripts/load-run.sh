#!/usr/bin/env bash
# Run the search capacity test (ops/load/search-peak.js) as a Kubernetes Job against the discovery ClusterIP Service,
# never through a port-forward (PLAN.md §15.2). It creates or updates the ConfigMap from ops/load/, deletes the
# previous Job, applies ops/load/k6-job.yaml with the chosen rate and duration, waits for the Job and prints its logs.
# Copies of the logs go to ops/load/results/, which git ignores. The previous Job is deleted only once a copy of its
# logs with k6's summary is there, or with --replace; --replace stops a running k6 with SIGINT, so it prints one.
#   Usage: scripts/load-run.sh [--rate N] [--duration 5m] [--replace] [--context-ok]
#   Uses the current kubectl context (or KUBECONFIG) and refuses one whose name contains neither "conferenceops" nor
#   "docker-desktop", unless --context-ok is passed. Needs kubectl and jq. Runs on the Mac's bash 3.2.
#   Tests: bash scripts/tests/load-run.test.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOAD_DIR="$ROOT/ops/load"
JOB_FILE="$LOAD_DIR/k6-job.yaml"
RESULTS_DIR="$LOAD_DIR/results"
SUMMARY_LINE='^{"report":"conferenceops-load-summary"'  # the JSON line search-peak.js prints when k6 stops
POLL_SECONDS=5
EXTRA_WAIT_SECONDS=600  # scheduling, the image pull, k6's 30 s graceful stop and the summary
STOP_WAIT_SECONDS=120   # --replace: how long a running k6 gets to stop after SIGINT and print its summary

# Set once in main, from the current context and the Job manifest.
CONTEXT=""
NAMESPACE=""
JOB=""

kc() { kubectl --context "$CONTEXT" --namespace "$NAMESPACE" "$@"; }
say() { echo "load-run: $*" >&2; }

usage() {
  cat <<'EOF'
Usage: scripts/load-run.sh [--rate N] [--duration 5m] [--replace] [--context-ok]
Runs ops/load/search-peak.js as the Kubernetes Job in ops/load/k6-job.yaml, against the discovery ClusterIP Service.
  --rate N        requests per second, a positive integer (default 8)
  --duration D    how long to offer the load, with h, m and s parts: 90s, 5m, 1h30m (default 5m)
  --replace       stop a load test that is still running and start this one (that changes the offered load). k6
                  gets SIGINT, so it prints its summary, and a copy of its logs is saved before its Job is deleted.
                  Also deletes a previous Job whose logs couldn't be saved with a summary
  --context-ok    allow a kubectl context whose name contains neither "conferenceops" nor "docker-desktop"
Exits with k6's exit code: 0 every threshold met, 99 a threshold crossed (reported only), 105 stopped early.
EOF
}

duration_seconds() {  # duration_seconds 1h30m -> 5400; fails on anything but h, m and s parts, in that order
  local rest="$1" total=0 n unit re='^([0-9]+h)?([0-9]+m)?([0-9]+s)?$'
  [[ -n "$rest" && "$rest" =~ $re ]] || return 1
  while [[ -n "$rest" ]]; do
    n="${rest%%[hms]*}"
    unit="${rest:${#n}:1}"
    rest="${rest:$((${#n} + 1))}"
    case "$unit" in
      h) total=$((total + 10#$n * 3600)) ;;
      m) total=$((total + 10#$n * 60)) ;;
      s) total=$((total + 10#$n)) ;;
    esac
  done
  ((total > 0)) || return 1
  echo "$total"
}

current_context() {  # current_context <context-ok>: prints the current context, unless it doesn't look like ours
  local ctx
  ctx=$(kubectl config current-context) || return 1
  if [[ "$ctx" != *conferenceops* && "$ctx" != *docker-desktop* && "$1" != true ]]; then
    say "refusing to load kubectl context '$ctx': its name contains neither 'conferenceops' nor 'docker-desktop'."
    say "Switch context, or pass --context-ok if this really is the cluster to load."
    return 1
  fi
  echo "$ctx"
}

check_target() {  # check_target <base-url>: the target Service exists; report its Ready endpoints
  local host="${1#*://}" ready
  host="${host%%/*}"
  host="${host%%:*}"
  if [[ "$host" == *.* ]]; then
    say "target $1 isn't a Service name in $NAMESPACE, so it isn't checked"
    return 0
  fi
  if ! kc get service "$host" >/dev/null; then
    say "Service $host doesn't exist in namespace $NAMESPACE: deploy the app first"
    return 1
  fi
  ready=$(kc get endpointslices -l "kubernetes.io/service-name=$host" \
    -o jsonpath='{range .items[?(@.addressType=="IPv4")].endpoints[*]}{.conditions.ready}{"\n"}{end}' \
    | grep -c '^true$' || true)
  say "target: Service $host with ${ready:-0} Ready endpoint(s)"
  if [[ "${ready:-0}" == 0 ]]; then say "warning: no Ready endpoints, so every request will fail"; fi
}

save_logs() {  # save_logs <job-uid> <file-prefix>: copy the Job's logs into ops/load/results/ and print the path
  local file="$RESULTS_DIR/$2-${1:0:8}.log"
  mkdir -p "$RESULTS_DIR"
  if ! kc logs "job/$JOB" >"$file"; then
    rm -f "$file"
    return 1
  fi
  echo "$file"
}

has_summary() { grep -q -- "$SUMMARY_LINE" "$1"; }  # has_summary <file>: it holds k6's end-of-test summary

saved_with_summary() {  # saved_with_summary <job-uid>: ops/load/results/ has a copy of this Job's logs with a summary
  local f
  for f in "$RESULTS_DIR"/*-"${1:0:8}".log; do
    if [[ -e "$f" ]] && has_summary "$f"; then return 0; fi
  done
  return 1
}

wait_for_job() {  # wait_for_job <job-uid> <timeout-seconds> [quiet]: prints Complete or Failed once the Job finished
  local selector="batch.kubernetes.io/controller-uid=$1" deadline=$((SECONDS + $2))
  local true_conditions='{range .status.conditions[?(@.status=="True")]}{.type} {end}'
  local state='.items[0].status.containerStatuses[0].state'
  local scheduled='.items[0].status.conditions[?(@.type=="PodScheduled")]'
  local job conditions pod started waiting unscheduled why detail
  local announced=false last_waiting="" last_why="" errors=0
  if [[ "${3:-}" == quiet ]]; then announced=true; fi
  while ((SECONDS < deadline)); do
    if ! job=$(kc get job "$JOB" -o jsonpath="{.metadata.uid}|$true_conditions"); then
      # A long run outlives API hiccups and token refreshes; give up only after half a minute of errors.
      errors=$((errors + 1))
      if ((errors >= 6)); then
        say "can't read Job $JOB; stopped waiting, but the test may still be running"
        return 1
      fi
      sleep "$POLL_SECONDS"
      continue
    fi
    errors=0
    if [[ "${job%%|*}" != "$1" ]]; then
      say "Job $JOB was replaced by another run; stopped waiting"
      return 1
    fi
    conditions="${job#*|}"
    case " $conditions " in
      *" Complete "*) echo Complete; return 0 ;;
      *" Failed "*) echo Failed; return 0 ;;
    esac
    # The container's state once the pod is on a node. Before that the pod has no container status, only the reason
    # it isn't scheduled (Unschedulable when no node has room for its requests).
    pod=$(kc get pods -l "$selector" \
      -o jsonpath="{$state.running.startedAt}|{$state.waiting.reason}|{$scheduled.reason}|{$scheduled.message}" \
      2>/dev/null || true)
    started="${pod%%|*}"
    pod="${pod#*|}"
    waiting="${pod%%|*}"
    unscheduled="${pod#*|}"
    why="${unscheduled%%|*}"
    detail="${unscheduled#*|}"
    if [[ -n "$started" && "$announced" == false ]]; then
      say "k6 running since $started"
      announced=true
    fi
    if [[ -n "$waiting" && "$waiting" != "$last_waiting" && "$waiting" != ContainerCreating ]]; then
      say "the k6 container is waiting: $waiting (see kubectl describe pod -l $selector)"
    fi
    last_waiting="$waiting"
    if [[ -n "$why" && "$why" != "$last_why" ]]; then
      say "the k6 pod isn't scheduled: $why${detail:+ ($detail)}; see kubectl describe pod -l $selector"
    fi
    last_why="$why"
    sleep "$POLL_SECONDS"
  done
  say "gave up after $2 s waiting for Job $JOB to finish; it may still be running"
  return 1
}

stop_running() {  # stop_running <job-uid>: stop the running k6 with SIGINT, so it prints its summary, and wait for it
  say "--replace: stopping the running load test with SIGINT, so k6 prints its summary before its Job is deleted"
  if ! kc exec "job/$JOB" -- kill -INT 1; then
    say "warning: couldn't send SIGINT to k6 (kubectl's error is above); is its container running?"
    return 0
  fi
  if ! wait_for_job "$1" "$STOP_WAIT_SECONDS" quiet >/dev/null; then
    say "nothing was deleted. Check Job $JOB before you run this again:"
    say "  kubectl --context $CONTEXT -n $NAMESPACE get job $JOB"
    return 1
  fi
}

keep_previous_logs() {  # keep_previous_logs <job-uid> <replace>: before a finished Job is deleted, keep its summary
  local copy problem
  # Its waiter, or an earlier run of this script, may have saved it already.
  if saved_with_summary "$1"; then return 0; fi
  # Otherwise its summary may be the only copy of an earlier run's evidence, for example after Ctrl-C.
  if copy=$(save_logs "$1" search-peak-previous); then
    if has_summary "$copy"; then
      say "saved the previous run's logs to ${copy#"$ROOT"/}"
      return 0
    elif [[ -s "$copy" ]]; then
      problem="the previous run's logs have no k6 summary (what they hold is in ${copy#"$ROOT"/})"
    else
      rm -f "$copy"  # kubectl prints nothing for a pod that never ran
      problem="the previous run's logs are empty (k6 may never have started)"
    fi
  else
    problem="couldn't copy the previous run's logs (kubectl's error is above)"
  fi
  if [[ "$2" == true ]]; then
    say "warning: $problem; --replace deletes Job $JOB anyway"
    return 0
  fi
  say "$problem, so Job $JOB is left in place."
  say "Look at it (kubectl --context $CONTEXT -n $NAMESPACE describe job $JOB), then pass --replace to delete it."
  return 1
}

finish() {  # finish <job-state> <k6-exit-code>: explain the outcome, and return k6's exit code
  case "$2" in
    0) say "Job $1: k6 exited 0, every threshold met." ;;
    99) say "Job $1: k6 exited 99, a threshold was crossed (see the summary); thresholds never cut a run short." ;;
    105) say "Job $1: k6 exited 105, stopped before the end of its duration." ;;
    '') say "Job $1, but the k6 container has no exit code; see kubectl describe job $JOB."; return 1 ;;
    *) say "Job $1: k6 exited $2; see the logs above." ;;
  esac
  return "$2"
}

main() {
  local rate=8 duration=5m replace=false context_ok=false
  while (($#)); do
    case "$1" in
      --rate | --duration)
        (($# >= 2)) || { say "$1 needs a value"; return 2; }
        if [[ "$1" == --rate ]]; then rate="$2"; else duration="$2"; fi
        shift 2
        ;;
      --rate=*) rate="${1#*=}"; shift ;;
      --duration=*) duration="${1#*=}"; shift ;;
      --replace) replace=true; shift ;;
      --context-ok) context_ok=true; shift ;;
      -h | --help) usage; return 0 ;;
      *) say "unknown argument: $1"; usage >&2; return 2 ;;
    esac
  done
  local re='^[1-9][0-9]*$' seconds
  [[ "$rate" =~ $re ]] || { say "--rate must be a positive integer (requests per second), got '$rate'"; return 2; }
  if ! seconds=$(duration_seconds "$duration"); then
    say "--duration must look like 90s, 5m or 1h30m, got '$duration'"
    return 2
  fi
  if ! command -v kubectl >/dev/null || ! command -v jq >/dev/null; then say "needs kubectl and jq"; return 1; fi

  CONTEXT=$(current_context "$context_ok") || return 1
  local manifest configmap base_url
  manifest=$(kubectl set env --local -f "$JOB_FILE" "RATE=$rate" "DURATION=$duration" -o json) || return 1
  if [[ -z "$manifest" ]]; then  # set env prints nothing when the file already has these values
    manifest=$(kubectl patch --local -f "$JOB_FILE" -p '{}' -o json) || return 1
  fi
  if ! jq -e --arg rate "$rate" --arg duration "$duration" '[.spec.template.spec.containers[0].env[]?
      | {(.name): .value}] | add | .RATE == $rate and .DURATION == $duration' >/dev/null <<<"$manifest"; then
    say "couldn't set RATE=$rate and DURATION=$duration in the Job from $JOB_FILE"
    return 1
  fi
  JOB=$(jq -r '.metadata.name // ""' <<<"$manifest")
  NAMESPACE=$(jq -r '.metadata.namespace // ""' <<<"$manifest")
  configmap=$(jq -r 'first(.spec.template.spec.volumes[]? | .configMap.name // empty) // ""' <<<"$manifest")
  base_url=$(jq -r 'first(.spec.template.spec.containers[0].env[]? | select(.name == "BASE_URL") | .value) // ""' \
    <<<"$manifest")
  if [[ -z "$JOB" || -z "$NAMESPACE" || -z "$configmap" ]]; then
    say "$JOB_FILE needs a name, a namespace and a ConfigMap volume"
    return 1
  fi
  say "context $CONTEXT, namespace $NAMESPACE, Job $JOB: $rate requests/s for $duration ($seconds s)"
  if ! kc get namespace "$NAMESPACE" >/dev/null; then
    say "can't read namespace $NAMESPACE (kubectl's error is above): is the cluster reachable and the app deployed?"
    return 1
  fi
  if [[ -n "$base_url" ]]; then check_target "$base_url" || return 1; fi

  local previous previous_uid
  previous=$(kc get job "$JOB" --ignore-not-found -o jsonpath='{.metadata.uid}|{.status.active}') || return 1
  if [[ -n "$previous" ]]; then
    previous_uid="${previous%%|*}"
    if [[ -n "${previous#*|}" && "${previous#*|}" != 0 ]]; then
      if [[ "$replace" != true ]]; then
        say "a load test is still running (Job $JOB). Stopping it would change the offered load mid-run."
        say "Wait for it to finish, or pass --replace to stop it and start this one."
        return 1
      fi
      stop_running "$previous_uid" || return 1
    fi
    keep_previous_logs "$previous_uid" "$replace" || return 1
  fi

  kc create configmap "$configmap" --from-file=search-peak.js="$LOAD_DIR/search-peak.js" \
    --from-file=search-summary.js="$LOAD_DIR/search-summary.js" \
    --from-file=queries.json="$LOAD_DIR/queries.json" --dry-run=client -o yaml | kc apply -f - || return 1
  kc delete job "$JOB" --ignore-not-found --cascade=foreground --wait=true --timeout=3m || return 1
  kc apply -f - <<<"$manifest" || return 1

  local uid state log_file code
  uid=$(kc get job "$JOB" -o jsonpath='{.metadata.uid}') || return 1
  say "Ctrl-C stops waiting, not the test. To end the test early and keep its summary:"
  say "  kubectl --context $CONTEXT -n $NAMESPACE exec job/$JOB -- kill -INT 1"
  state=$(wait_for_job "$uid" $((seconds + EXTRA_WAIT_SECONDS))) || return 1

  if log_file=$(save_logs "$uid" "search-peak-$(date -u +%Y%m%dT%H%M%SZ)-${rate}rps"); then
    cat "$log_file"
    say "logs saved to ${log_file#"$ROOT"/}"
  else
    say "couldn't read the Job's logs"
  fi
  code=$(kc get pods -l "batch.kubernetes.io/controller-uid=$uid" \
    -o jsonpath='{.items[0].status.containerStatuses[0].state.terminated.exitCode}' 2>/dev/null || true)
  finish "$state" "$code"
}

main "$@"; exit
