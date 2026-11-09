#!/usr/bin/env bash
# What the chart promises, checked on its rendered AKS manifest before a merge (the helm-policy CI job). kubeconform
# checks only the schema; the deploy relies on these:
#   - K8S-1 for every container of every workload (Deployment, StatefulSet, DaemonSet, Job, CronJob): non-root, no
#     privilege escalation, all capabilities dropped, a read-only root filesystem, CPU and memory requests and a memory
#     limit.
#   - The public entry point: one LoadBalancer Service. It selects the web pods, with externalTrafficPolicy Cluster
#     (every node forwards to a Ready web pod, so a rollout never waits on Azure's probe), and Azure's probe is HTTP
#     (appProtocol http) on the web pods' readiness path, a static nginx location.
#   - The web pods: an emptyDir at /etc/nginx/conf.d (the entrypoint renders the nginx config there at start) and one
#     at /tmp (pid and temp files). Under the read-only root, nginx can't start without them.
#   Usage: helm template ... | scripts/check-chart-policy.sh        or        scripts/check-chart-policy.sh <manifest>
#   Tests: bash scripts/tests/check-chart-policy.test.sh
# Ruby's standard YAML library (preinstalled on GitHub's Ubuntu runners and on macOS) reads the manifest, as in
# check-incident-scope.sh. Prints each broken promise on its own line and exits 1, as it does for a manifest it can't
# read.
set -euo pipefail

policy_program() {
  cat <<'RUBY'
require "yaml"

WORKLOAD_KINDS = %w[Deployment StatefulSet DaemonSet Job CronJob].freeze
PROBE_PATH = "service.beta.kubernetes.io/azure-load-balancer-health-probe-request-path"
COMPONENT = "app.kubernetes.io/component"
WEB_WRITABLE = {
  "/etc/nginx/conf.d" => "the entrypoint renders the nginx config there",
  "/tmp" => "nginx writes its pid and temp files there",
}.freeze

def name_of(doc)
  "#{doc["kind"]}/#{doc.dig("metadata", "name")}"
end

def pod_spec(doc)
  spec = doc["spec"] || {}
  spec = spec.dig("jobTemplate", "spec") || {} if doc["kind"] == "CronJob"
  spec.dig("template", "spec") || {}
end

def containers_of(pod)
  Array(pod["initContainers"]) + Array(pod["containers"])
end

# K8S-1 for one container; the pod's securityContext supplies runAsNonRoot when the container doesn't set it.
def container_problems(where, pod, container)
  context = container["securityContext"] || {}
  pod_context = pod["securityContext"] || {}
  non_root = context.key?("runAsNonRoot") ? context["runAsNonRoot"] : pod_context["runAsNonRoot"]
  requests = container.dig("resources", "requests") || {}
  limits = container.dig("resources", "limits") || {}
  problems = []
  problems << "#{where}: runAsNonRoot isn't true" unless non_root == true
  problems << "#{where}: allowPrivilegeEscalation isn't false" unless context["allowPrivilegeEscalation"] == false
  problems << "#{where}: capabilities don't drop ALL" unless Array(context.dig("capabilities", "drop")).include?("ALL")
  problems << "#{where}: the root filesystem isn't read-only" unless context["readOnlyRootFilesystem"] == true
  problems << "#{where}: no CPU or memory request" unless requests["cpu"] && requests["memory"]
  problems << "#{where}: no memory limit" unless limits["memory"]
  problems
end

def public_problems(service, web_labels, ready_paths)
  where = name_of(service)
  selector = service.dig("spec", "selector") || {}
  policy = service.dig("spec", "externalTrafficPolicy") || "Cluster"  # the Kubernetes default
  probe = (service.dig("metadata", "annotations") || {})[PROBE_PATH]
  problems = []
  if selector.empty? || !web_labels || !selector.all? { |key, value| web_labels[key] == value }
    problems << "#{where}: doesn't select the web pods (its selector has component #{selector[COMPONENT] || "none"})"
  end
  unless policy == "Cluster"
    problems << "#{where}: externalTrafficPolicy is #{policy}, not Cluster. Azure would follow the web pod from node " \
                "to node, and new connections fail until its probe notices each move"
  end
  Array(service.dig("spec", "ports")).each do |port|
    next if port["appProtocol"] == "http"
    problems << "#{where} port #{port["name"] || port["port"]}: appProtocol is #{port["appProtocol"] || "unset"}, not " \
                "http, so Azure ignores the probe path"
  end
  unless ready_paths.include?(probe)
    problems << "#{where}: Azure's probe path is #{probe || "unset"}, not the web pods' readiness path " \
                "(#{ready_paths.join(", ")})"
  end
  problems
end

def web_problems(web)
  pod = pod_spec(web)
  empty_dirs = Array(pod["volumes"]).select { |volume| volume.key?("emptyDir") }.map { |volume| volume["name"] }
  mounts = Array(pod["containers"]).flat_map { |container| Array(container["volumeMounts"]) }
  WEB_WRITABLE.reject do |path, _why|
    mounts.any? { |mount| mount["mountPath"] == path && empty_dirs.include?(mount["name"]) && !mount["readOnly"] }
  end.map do |path, why|
    "#{name_of(web)}: no writable emptyDir at #{path} (#{why}, and the root filesystem is read-only)"
  end
end

begin
  # The manifest is this chart's own render. load_stream reads every document (safe_load reads only the first).
  docs = YAML.load_stream(ARGF.read).select { |doc| doc.is_a?(Hash) }
  workloads = docs.select { |doc| WORKLOAD_KINDS.include?(doc["kind"]) }
  webs = workloads.select do |doc|
    doc["kind"] == "Deployment" && doc.dig("spec", "template", "metadata", "labels", COMPONENT) == "web"
  end
  services = docs.select { |doc| doc["kind"] == "Service" && doc.dig("spec", "type") == "LoadBalancer" }
  ready_paths = webs.flat_map { |web| Array(pod_spec(web)["containers"]) }
                    .map { |container| container.dig("readinessProbe", "httpGet", "path") }.compact.uniq

  problems = []
  containers = 0
  workloads.each do |doc|
    pod = pod_spec(doc)
    containers_of(pod).each do |container|
      containers += 1
      problems.concat(container_problems("#{name_of(doc)} container #{container["name"]}", pod, container))
    end
  end
  problems << "no workload in the manifest" if workloads.empty?
  problems << "no Deployment runs the web pods (#{COMPONENT}: web)" if webs.empty?
  problems << "the web pods have no HTTP readiness probe" if !webs.empty? && ready_paths.empty?
  webs.each { |web| problems.concat(web_problems(web)) }
  unless services.size == 1
    problems << "#{services.size} LoadBalancer Services; the chart has one, the public entry point"
  end
  web_labels = webs.first&.dig("spec", "template", "metadata", "labels")
  services.each { |service| problems.concat(public_problems(service, web_labels, ready_paths)) }

  if problems.empty?
    puts "Chart policy OK: #{containers} containers in #{workloads.size} workloads meet K8S-1. " \
         "#{name_of(services.first)} selects the web pods, with externalTrafficPolicy Cluster and an HTTP probe on " \
         "#{ready_paths.join(", ")}. The web pods mount emptyDirs at #{WEB_WRITABLE.keys.join(" and ")}."
    exit 0
  end
  puts problems
  exit 1
rescue StandardError => e # exit raises SystemExit, which this doesn't catch
  $stderr.puts "#{e.class}: #{e.message}" # not warn: -W0 silences it
  exit 2
end
RUBY
}

main() {
  if ! command -v ruby >/dev/null; then
    echo "::error::ruby isn't installed. It reads the rendered manifest."
    return 1
  fi
  local report rc=0
  report=$(ruby -W0 -e "$(policy_program)" "$@" 2>&1) || rc=$?
  case "$rc" in
    0) printf '%s\n' "$report"; return 0 ;;
    1) echo "::error::The rendered chart breaks what the deploy relies on (K8S-1, the public entry point):" ;;
    *) echo "::error::Couldn't read the rendered manifest:" ;;
  esac
  # Indented, so no line of the report can start a workflow command.
  printf '%s\n' "$report" | sed 's/^/  /'
  return 1
}

main "$@"; exit
