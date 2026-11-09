#!/usr/bin/env bash
# Mark a deploy in Dash0: one `dash0.deployment` event per service the chart deploys (web, core-api, discovery-api
# and worker). Dash0's deployment-event convention asks for the event once per deployment, after it completed,
# successfully or not, with deployment.status succeeded or failed
# (https://www.dash0.com/docs/dash0/opentelemetry/semconvs/events/deployment). The deploy workflow runs this from its
# success step and from its failure step, so a failed deploy leaves a marker too.
#   Usage: scripts/dash0-deploy-marker.sh <succeeded|failed> <tag>
#     <tag>  the image tag that was deployed, or that failed to deploy: the commit SHA.
#   Needs the dash0 CLI and, in the environment, DASH0_OTLP_URL, DASH0_AUTH_TOKEN (the ingest token) and
#   DASH0_DATASET, which the CLI reads itself, and GITHUB_SERVER_URL and GITHUB_REPOSITORY (set by GitHub Actions)
#   for the repository URL on the event. The token is never printed.
#   Tests: scripts/tests/dash0-deploy-marker.test.sh
set -euo pipefail

SERVICES=(web core-api discovery-api worker)
USAGE="usage: scripts/dash0-deploy-marker.sh <succeeded|failed> <tag>"

die() {
  echo "dash0-deploy-marker: $*" >&2
  exit 1
}

require() {
  local name
  for name in DASH0_OTLP_URL DASH0_AUTH_TOKEN DASH0_DATASET GITHUB_SERVER_URL GITHUB_REPOSITORY; do
    [[ -n "${!name:-}" ]] || die "$name is not set"
  done
  command -v dash0 >/dev/null || die "the dash0 CLI is not installed"
}

main() {
  local status="${1:-}" tag="${2:-}" body severity service
  if (($# != 2)) || [[ -z "$tag" ]]; then die "$USAGE"; fi
  case "$status" in
    succeeded) body="Deployed $tag" severity=9 ;;         # INFO
    failed) body="Deploy of $tag failed" severity=17 ;;   # ERROR
    *) die "$USAGE" ;;
  esac
  require
  for service in "${SERVICES[@]}"; do
    dash0 logs send "$body" --event-name dash0.deployment --severity-number "$severity" \
      --resource-attribute "service.name=$service" \
      --resource-attribute "service.namespace=conferenceops" \
      --resource-attribute "service.version=$tag" \
      --resource-attribute "deployment.environment.name=aks" \
      --log-attribute "deployment.status=$status" \
      --log-attribute "vcs.repository.url.full=$GITHUB_SERVER_URL/$GITHUB_REPOSITORY" \
      --log-attribute "vcs.ref.head.revision=$tag"
  done
  echo "Marked the deploy of $tag as $status in Dash0 (dataset $DASH0_DATASET) for ${SERVICES[*]}"
}

main "$@"; exit
