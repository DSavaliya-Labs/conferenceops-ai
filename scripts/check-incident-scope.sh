#!/usr/bin/env bash
# OPS-1: an incident pull request may change only the discovery workload's replicas or resources in
# deploy/env/aks/values.yaml. Used by the incident-scope CI job.
#   IS_INCIDENT  "true" for a PR labeled `incident` or authored by dash0-app[bot]
#   BASE_SHA     the PR's base commit
set -euo pipefail

ALLOWED_FILE="deploy/env/aks/values.yaml"
# Changed lines may only touch these keys (or be comments and blank lines).
ALLOWED_KEYS='replicas|replicaCount|resources|requests|limits|cpu|memory|discovery'

main() {
  if [[ "${IS_INCIDENT:-false}" != "true" ]]; then
    echo "Not an incident pull request: nothing to check."
    return 0
  fi
  [[ -n "${BASE_SHA:-}" ]] || { echo "::error::BASE_SHA is not set"; return 1; }

  local files
  files=$(git diff --name-only "$BASE_SHA"...HEAD) || return 1
  local bad=0 f
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    if [[ "$f" != "$ALLOWED_FILE" ]]; then
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

  if ((bad)); then return 1; fi
  echo "Incident scope OK: only discovery replicas/resources in $ALLOWED_FILE changed."
}

main "$@"; exit
