#!/usr/bin/env bash
# OPS-1: an incident pull request may change only the discovery workload's replicas or resources in
# deploy/env/aks/values.yaml. Used by the incident-scope CI job.
#   IS_INCIDENT  "true" for a PR labeled `incident` or authored by dash0-app[bot]
#   BASE_SHA     the PR's base commit
# Three checks: the changed files, the first key on each changed line, and the parsed values. The line check alone
# let a flow-style mapping such as `discovery: {replicas: 3, command: worker}` through, so Ruby's standard YAML
# library (preinstalled on GitHub's Ubuntu runners and on macOS) loads both versions of the file, and only
# components.discovery.replicas and components.discovery.resources may differ.
set -euo pipefail

ALLOWED_FILE="deploy/env/aks/values.yaml"
# Changed lines may only touch these keys (or be comments and blank lines). The chart reads `replicas`, not
# `replicaCount`.
ALLOWED_KEYS='replicas|resources|requests|limits|cpu|memory|discovery'
TMP=""

# Ruby: loads the base and head values (paths in ARGV) and prints, as JSON, each path that differs outside
# components.discovery.replicas and components.discovery.resources. Exits 0 when nothing else differs, 1 when
# something does, and 2 when a version can't be loaded. Anchors and merge keys are resolved, and YAML 1.1 scalars
# (yes, on, 017) load the way Helm reads them.
values_diff_program() {
  cat <<'RUBY'
require "date"
require "json"
require "yaml"

ALLOWED = [%w[components discovery replicas], %w[components discovery resources]].freeze

def load_values(path)
  YAML.safe_load(File.read(path), permitted_classes: [Date, Time, Symbol], aliases: true)
rescue StandardError => e # Psych's syntax, class and alias errors, and anything else that stops the load
  $stderr.puts "#{File.basename(path)}: #{e.class}: #{e.message}" # not warn: -W0 silences it
  exit 2
end

def without_allowed(values)
  ALLOWED.each do |*parents, key|
    holder = parents.reduce(values) { |node, name| node.is_a?(Hash) ? node[name] : nil }
    holder.delete(key) if holder.is_a?(Hash)
  end
  values
end

def leaves(node, path = [], out = {})
  if node.is_a?(Hash) && !node.empty?
    node.each { |name, value| leaves(value, path + [name], out) }
  elsif node.is_a?(Array) && !node.empty?
    node.each_with_index { |value, i| leaves(value, path + [i], out) }
  else
    out[path] = node
  end
  out
end

begin
  base, head = ARGV.map { |path| without_allowed(load_values(path)) }
  exit 0 if base == head
  before = leaves(base)
  after = leaves(head)
  differ = (before.keys | after.keys).reject do |path|
    before.key?(path) && after.key?(path) && before[path] == after[path]
  end
  # An emptied mapping (discovery once replicas and resources are set aside) isn't news when a field appears under it.
  differ.reject! { |path| differ.any? { |other| other.length > path.length && other.first(path.length) == path } }
  differ.each do |path|
    change = if !before.key?(path) then "added" elsif !after.key?(path) then "removed" else "changed" end
    puts "#{change}: #{JSON.generate(path.map(&:to_s))}"
  end
  exit 1
rescue StandardError => e # exit raises SystemExit, which this doesn't catch
  $stderr.puts "#{e.class}: #{e.message}"
  exit 2
end
RUBY
}

# Compares the parsed values file at the merge base and at HEAD. Prints why and fails when anything other than the
# discovery replicas and resources differs, or when a version is missing or can't be loaded.
check_values() {
  if ! command -v ruby >/dev/null; then
    echo "::error::ruby isn't installed. It loads $ALLOWED_FILE to compare the values (OPS-1)."
    return 1
  fi
  local base
  if ! base=$(git merge-base "$BASE_SHA" HEAD); then
    echo "::error::No merge base between $BASE_SHA and HEAD."
    return 1
  fi
  TMP=$(mktemp -d) || { echo "::error::Couldn't create a temporary directory."; return 1; }
  if ! git show "$base:$ALLOWED_FILE" >"$TMP/base.yaml" 2>/dev/null; then
    echo "::error file=$ALLOWED_FILE::$ALLOWED_FILE isn't on the base branch; an incident PR can't add it (OPS-1)."
    return 1
  fi
  if ! git show "HEAD:$ALLOWED_FILE" >"$TMP/head.yaml" 2>/dev/null; then
    echo "::error file=$ALLOWED_FILE::An incident PR may not delete $ALLOWED_FILE (OPS-1)."
    return 1
  fi
  local report rc=0
  report=$(ruby -W0 -e "$(values_diff_program)" "$TMP/base.yaml" "$TMP/head.yaml" 2>&1) || rc=$?
  case "$rc" in
    0) return 0 ;;
    1) echo "::error file=$ALLOWED_FILE::Values changed outside components.discovery.replicas/resources (OPS-1):" ;;
    *) echo "::error file=$ALLOWED_FILE::Couldn't load $ALLOWED_FILE to compare the values:" ;;
  esac
  # Indented, so no line of the report can start a workflow command.
  printf '%s\n' "$report" | sed 's/^/  /'
  return 1
}

main() {
  if [[ "${IS_INCIDENT:-false}" != "true" ]]; then
    echo "Not an incident pull request: nothing to check."
    return 0
  fi
  [[ -n "${BASE_SHA:-}" ]] || { echo "::error::BASE_SHA is not set"; return 1; }
  trap '[[ -z "$TMP" ]] || rm -rf -- "$TMP"' EXIT

  local files
  files=$(git diff --name-only "$BASE_SHA"...HEAD) || return 1
  local bad=0 values_changed=0 f
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    if [[ "$f" == "$ALLOWED_FILE" ]]; then
      values_changed=1
    else
      echo "::error file=$f::An incident PR may only change $ALLOWED_FILE (OPS-1)."
      bad=1
    fi
  done <<<"$files"

  local lines
  lines=$(git diff -U0 "$BASE_SHA"...HEAD -- "$ALLOWED_FILE" | grep -E '^[+-]' | grep -vE '^(\+\+\+|---) ' || true)
  local line
  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    local content="${line:1}"
    if [[ "$content" =~ ^[[:space:]]*(#.*)?$ ]]; then continue; fi
    if ! [[ "$content" =~ ^[[:space:]]*($ALLOWED_KEYS)[[:space:]]*: ]]; then
      echo "::error file=$ALLOWED_FILE::Changed line outside replicas/resources: ${content}"
      bad=1
    fi
  done <<<"$lines"

  if ((values_changed)); then
    check_values || bad=1
  fi

  if ((bad)); then return 1; fi
  echo "Incident scope OK: only discovery replicas/resources in $ALLOWED_FILE changed."
}

main "$@"; exit
