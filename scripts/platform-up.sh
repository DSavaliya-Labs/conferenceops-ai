#!/usr/bin/env bash
# Cluster-wide pieces the app chart relies on, installed once per cluster. Idempotent.
#   - the ECK operator, which runs Elasticsearch for the chart;
#   - the `conferenceops` namespace.
# Uses the current kubectl context (or KUBECONFIG); the deploy workflow points it at AKS.
set -euo pipefail

ECK_VERSION="3.5.0"
NAMESPACE="conferenceops"

main() {
  helm repo add elastic https://helm.elastic.co --force-update >/dev/null
  helm upgrade --install elastic-operator elastic/eck-operator --version "$ECK_VERSION" \
    --namespace elastic-system --create-namespace --reset-values --wait --timeout 5m
  kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
  echo "platform ready: ECK $ECK_VERSION, namespace $NAMESPACE"
}

main "$@"; exit
