#!/usr/bin/env bash
# Tests for scripts/check-chart-policy.sh. Renders the chart with the AKS values, as the helm-policy CI job does, and
# checks that the policy passes on that render and fails, naming the problem, on renders that each break one promise:
# through a chart value where one exists (public.target, public.enabled), otherwise by editing the rendered manifest.
# Needs helm and ruby, and touches nothing outside a temporary directory.
#   Usage: bash scripts/tests/check-chart-policy.test.sh        Exits non-zero when a check fails.
#   CHECK_SCRIPT=<path> runs the same cases against another copy of the script, to see which cases catch a bug.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
REPO_ROOT=$(cd "$HERE/../.." && pwd -P)
SCRIPT="${CHECK_SCRIPT:-$HERE/../check-chart-policy.sh}"
WORK="" CASE="" CURRENT="" BIN_NO_RUBY="" RC=0 PASSED=0 FAILED=0

# --- Harness --------------------------------------------------------------------------------------------------------

render() {  # render [helm args...]: the chart with the AKS values and the image values CI uses
  helm template conferenceops "$REPO_ROOT/deploy/chart" -f "$REPO_ROOT/deploy/env/aks/values.yaml" \
    --set image.repository=example.azurecr.io/conferenceops-backend --set image.tag=ci \
    --set web.image.repository=example.azurecr.io/conferenceops-web --set web.image.tag=ci "$@"
}

begin() {  # begin <case> [helm args...]: the case's manifest is the AKS render, or a render with these extra values
  CURRENT="$1"
  CASE="$WORK/cases/$1"
  shift
  mkdir -p "$CASE"
  if (($#)); then render "$@" >"$CASE/manifest.yaml"; else cp "$WORK/aks.yaml" "$CASE/manifest.yaml"; fi
}

# Ruby that loads the case's manifest (ARGV[0]), runs the snippet (ARGV[1]) and prints the documents again. The
# snippet gets doc(kind, name) and pod(kind, name); a document it names that isn't there stops the run.
EDIT_PROGRAM='
require "yaml"
DOCS = YAML.load_stream(File.read(ARGV[0])).select { |d| d.is_a?(Hash) }
def doc(kind, name)
  DOCS.find { |d| d["kind"] == kind && d.dig("metadata", "name") == name } || abort("no #{kind}/#{name}")
end
def pod(kind, name)
  doc(kind, name)["spec"]["template"]["spec"]
end
eval(ARGV[1])
print DOCS.map(&:to_yaml).join
'

edit() {  # edit <ruby>: change the case's manifest. An edit that fails is a broken test, so it stops the run.
  if ! ruby -W0 -e "$EDIT_PROGRAM" "$CASE/manifest.yaml" "$1" >"$CASE/edited.yaml" 2>"$CASE/edit.err"; then
    echo "BROKEN TEST [$CURRENT]: the edit failed: $(cat "$CASE/edit.err")" >&2
    exit 1
  fi
  mv "$CASE/edited.yaml" "$CASE/manifest.yaml"
}

run() {  # run [NAME=value]...: the check on the case's manifest, as the CI job runs it; sets RC and $CASE/out
  RC=0
  env "$@" bash "$SCRIPT" "$CASE/manifest.yaml" >"$CASE/out" 2>&1 || RC=$?
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

broken() {  # broken <text>: the check failed, named this problem, and didn't report OK
  expect_rc 1
  contains "::error::The rendered chart breaks what the deploy relies on"
  contains "$1"
  lacks "Chart policy OK"
}

# --- Tests ----------------------------------------------------------------------------------------------------------

test_the_aks_render_passes() {
  begin aks-render
  run
  expect_rc 0
  contains "Chart policy OK: "
  contains " meet K8S-1."
  contains "Service/conferenceops-public selects the web pods, with externalTrafficPolicy Cluster and an HTTP probe on /health/ready."
  contains "The web pods mount emptyDirs at /etc/nginx/conf.d and /tmp."
}

test_public_traffic_to_core() {  # public.target back to core: the URL would serve the API, not the app
  begin public-to-core --set public.target=core
  run
  broken "Service/conferenceops-public: doesn't select the web pods (its selector has component core)"
}

test_no_public_entry_point() {
  begin no-public --set public.enabled=false
  run
  broken "0 LoadBalancer Services; the chart has one, the public entry point"
}

test_local_traffic_policy() {  # regression: with Local, rollouts moved the only web pod and requests failed meanwhile
  begin local-policy
  edit 'doc("Service", "conferenceops-public")["spec"]["externalTrafficPolicy"] = "Local"'
  run
  broken "Service/conferenceops-public: externalTrafficPolicy is Local, not Cluster."
}

test_unset_traffic_policy_is_cluster() {  # Kubernetes defaults it to Cluster
  begin unset-policy
  edit 'doc("Service", "conferenceops-public")["spec"].delete("externalTrafficPolicy")'
  run
  expect_rc 0
  contains "Chart policy OK"
}

test_probe_without_app_protocol() {  # regression: Azure then ignores the probe path annotation
  begin no-app-protocol
  edit 'doc("Service", "conferenceops-public")["spec"]["ports"][0].delete("appProtocol")'
  run
  broken "Service/conferenceops-public port http: appProtocol is unset, not http, so Azure ignores the probe path"
}

test_probe_path_not_readiness() {  # a probe through the proxy would make the load balancer wait on core-api
  begin probe-path
  edit 'doc("Service", "conferenceops-public")["metadata"]["annotations"][
          "service.beta.kubernetes.io/azure-load-balancer-health-probe-request-path"] = "/api/v1/demo/profiles"'
  run
  broken "Service/conferenceops-public: Azure's probe path is /api/v1/demo/profiles, not the web pods' readiness path (/health/ready)"
}

test_web_without_conf_d() {  # nginx can't start: the entrypoint can't render its config under a read-only root
  begin web-no-conf-d
  edit 'pod("Deployment", "conferenceops-web")["containers"][0]["volumeMounts"].reject! { |m| m["mountPath"] == "/etc/nginx/conf.d" }'
  run
  broken "Deployment/conferenceops-web: no writable emptyDir at /etc/nginx/conf.d (the entrypoint renders the nginx config there"
  lacks "no writable emptyDir at /tmp"
}

test_web_tmp_read_only() {
  begin web-tmp-read-only
  edit 'pod("Deployment", "conferenceops-web")["containers"][0]["volumeMounts"].find { |m| m["mountPath"] == "/tmp" }["readOnly"] = true'
  run
  broken "Deployment/conferenceops-web: no writable emptyDir at /tmp (nginx writes its pid and temp files there"
}

test_web_tmp_not_an_empty_dir() {
  begin web-tmp-config-map
  edit 'volume = pod("Deployment", "conferenceops-web")["volumes"].find { |v| v["name"] == "tmp" }
        volume.delete("emptyDir")
        volume["configMap"] = {"name" => "conferenceops-collector"}'
  run
  broken "Deployment/conferenceops-web: no writable emptyDir at /tmp"
}

test_writable_root_filesystem() {
  begin writable-root
  edit 'pod("Deployment", "conferenceops-web")["containers"][0]["securityContext"]["readOnlyRootFilesystem"] = false'
  run
  broken "Deployment/conferenceops-web container web: the root filesystem isn't read-only"
}

test_privilege_escalation() {  # the collector comes from a subchart, and it is checked too
  begin privilege-escalation
  edit 'pod("Deployment", "conferenceops-collector")["containers"][0]["securityContext"]["allowPrivilegeEscalation"] = true'
  run
  broken "Deployment/conferenceops-collector container collector: allowPrivilegeEscalation isn't false"
}

test_capabilities_kept() {
  begin capabilities
  edit 'pod("Job", "conferenceops-seed")["containers"][0]["securityContext"]["capabilities"] = {"drop" => ["NET_RAW"]}'
  run
  broken "Job/conferenceops-seed container seed: capabilities don't drop ALL"
}

test_no_memory_limit() {
  begin no-memory-limit
  edit 'pod("Job", "conferenceops-migrate")["containers"][0]["resources"].delete("limits")'
  run
  broken "Job/conferenceops-migrate container migrate: no memory limit"
}

test_no_requests() {
  begin no-requests
  edit 'pod("StatefulSet", "conferenceops-postgres")["containers"][0]["resources"]["requests"].delete("cpu")'
  run
  broken "StatefulSet/conferenceops-postgres container postgres: no CPU or memory request"
}

test_container_runs_as_root() {  # the container's runAsNonRoot overrides the pod's
  begin root-container
  edit 'pod("Deployment", "conferenceops-worker")["containers"][0]["securityContext"]["runAsNonRoot"] = false'
  run
  broken "Deployment/conferenceops-worker container worker: runAsNonRoot isn't true"
}

test_pod_runs_as_root() {  # no runAsNonRoot on the container or the pod
  begin root-pod
  edit 'pod("Deployment", "conferenceops-discovery")["securityContext"].delete("runAsNonRoot")'
  run
  broken "Deployment/conferenceops-discovery container discovery: runAsNonRoot isn't true"
}

test_init_container() {  # init containers are held to K8S-1 as well
  begin init-container
  edit 'pod("Deployment", "conferenceops-web")["initContainers"] = [{"name" => "warm", "image" => "busybox:1.37"}]'
  run
  broken "Deployment/conferenceops-web container warm: the root filesystem isn't read-only"
  contains "Deployment/conferenceops-web container warm: no memory limit"
}

test_cron_job() {  # a workload kind the chart doesn't have yet
  begin cron-job
  edit 'DOCS << {"apiVersion" => "batch/v1", "kind" => "CronJob", "metadata" => {"name" => "nightly"},
                 "spec" => {"schedule" => "0 3 * * *", "jobTemplate" => {"spec" => {"template" => {"spec" => {
                   "restartPolicy" => "Never", "containers" => [{"name" => "report", "image" => "busybox:1.37"}]}}}}}}'
  run
  broken "CronJob/nightly container report: allowPrivilegeEscalation isn't false"
}

test_unreadable_manifest() {
  begin unreadable
  printf 'kind: Service\nmetadata: {name: [unclosed\n' >"$CASE/manifest.yaml"
  run
  expect_rc 1
  contains "::error::Couldn't read the rendered manifest:"
  lacks "Chart policy OK"
}

test_empty_manifest() {  # a render that failed upstream must not pass as "nothing to check"
  begin empty
  : >"$CASE/manifest.yaml"
  run
  broken "no workload in the manifest"
}

test_needs_ruby() {  # without ruby the manifest can't be read, so the check fails closed
  begin no-ruby
  run PATH="$BIN_NO_RUBY"
  expect_rc 1
  contains "ruby isn't installed"
}

main() {
  local tool
  for tool in helm ruby; do
    command -v "$tool" >/dev/null || { echo "These tests need $tool." >&2; return 1; }
  done
  WORK=$(cd "$(mktemp -d "${TMPDIR:-/tmp}/check-chart-policy-test.XXXXXX")" && pwd -P)
  trap 'rm -rf -- "$WORK"' EXIT
  render >"$WORK/aks.yaml"
  BIN_NO_RUBY="$WORK/bin/no-ruby"
  make_bin "$BIN_NO_RUBY" bash cat sed

  test_the_aks_render_passes
  test_public_traffic_to_core
  test_no_public_entry_point
  test_local_traffic_policy
  test_unset_traffic_policy_is_cluster
  test_probe_without_app_protocol
  test_probe_path_not_readiness
  test_web_without_conf_d
  test_web_tmp_read_only
  test_web_tmp_not_an_empty_dir
  test_writable_root_filesystem
  test_privilege_escalation
  test_capabilities_kept
  test_no_memory_limit
  test_no_requests
  test_container_runs_as_root
  test_pod_runs_as_root
  test_init_container
  test_cron_job
  test_unreadable_manifest
  test_empty_manifest
  test_needs_ruby

  echo "check-chart-policy tests: $PASSED checks passed, $FAILED failed."
  ((FAILED == 0))
}

main "$@"; exit
