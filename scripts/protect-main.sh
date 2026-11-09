#!/usr/bin/env bash
# Create or update the `main` ruleset: a pull request, the eight required checks, and one approval (Gitar's) that a
# new push dismisses; no force-push or deletion. Idempotent.
#   Usage: scripts/protect-main.sh        Needs gh with admin rights on the repository.
set -euo pipefail

REPO="${GH_REPO:-DSavaliya-Labs/conferenceops-ai}"
NAME="main"
CHECKS=(quality domain-tests data-integration api-contract ui-smoke-accessibility helm-policy incident-scope build-images)

ruleset_json() {
  local checks_json
  checks_json=$(printf '%s\n' "${CHECKS[@]}" | jq -R '{context: .}' | jq -s .)
  jq -n --arg name "$NAME" --argjson checks "$checks_json" '{
    name: $name,
    target: "branch",
    enforcement: "active",
    conditions: {ref_name: {include: ["~DEFAULT_BRANCH"], exclude: []}},
    rules: [
      {type: "deletion"},
      {type: "non_fast_forward"},
      {type: "pull_request", parameters: {
        required_approving_review_count: 1,
        dismiss_stale_reviews_on_push: true,
        require_code_owner_review: false,
        require_last_push_approval: false,
        required_review_thread_resolution: false,
        allowed_merge_methods: ["merge"]}},
      {type: "required_status_checks", parameters: {
        strict_required_status_checks_policy: false,
        required_status_checks: $checks}}
    ]}'
}

main() {
  local id
  id=$(gh api "repos/$REPO/rulesets" --jq ".[] | select(.name == \"$NAME\") | .id" | head -1) || return 1
  if [[ -n "$id" ]]; then
    ruleset_json | gh api -X PUT "repos/$REPO/rulesets/$id" --input - --jq '"updated ruleset \(.id)"'
  else
    ruleset_json | gh api -X POST "repos/$REPO/rulesets" --input - --jq '"created ruleset \(.id)"'
  fi
}

main "$@"; exit
