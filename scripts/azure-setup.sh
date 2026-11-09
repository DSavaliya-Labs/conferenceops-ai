#!/usr/bin/env bash
# One-time Azure and GitHub wiring for deploying to AKS. Idempotent: safe to run again.
#   - a container registry attached to the AKS cluster;
#   - an Entra app that GitHub Actions signs in as via OIDC (no stored Azure secret), trusted only for this
#     repository's `aks` environment, with the roles it needs to push images and deploy;
#   - the Kubernetes admin role for whoever runs this, so kubectl works through Entra ID;
#   - the repository's `aks` environment (main only) and its variables. `azure-setup.sh secrets` sets its secrets.
# Secrets come from the environment (load them with `. ~/.config/conferenceops/env.sh`); they are never printed.
# Hackathon sandbox subscription only.
set -euo pipefail

SUB="${AZURE_SUBSCRIPTION_ID:?load ~/.config/conferenceops/env.sh first}"
EXPECTED_SUB_NAME="Azure subscription 1 (PayGo)"
RG="rg-kubecon-hack"
LOC="westus3"
AKS="aks-conferenceops"
ACR="cnfops${SUB:0:6}"
APP="conferenceops-ai-github"
REPO="DSavaliya-Labs/conferenceops-ai"
ENV_NAME="aks"

azs() { az "$@" --subscription "$SUB"; }

check_subscription() {
  local name
  name=$(az account show --subscription "$SUB" --query name -o tsv) || return 1
  [[ "$name" == "$EXPECTED_SUB_NAME" ]] || { echo "refusing: subscription is '$name'" >&2; return 1; }
}

registry() {
  if ! azs acr show -n "$ACR" -g "$RG" -o none 2>/dev/null; then
    azs acr create -n "$ACR" -g "$RG" -l "$LOC" --sku Basic -o none
  fi
  azs aks update -g "$RG" -n "$AKS" --attach-acr "$ACR" -o none
  echo "registry: $ACR attached to $AKS"
}

github_identity() {
  local app_id sp_id org_id repo_id aks_id acr_id
  app_id=$(az ad app list --display-name "$APP" --query "[0].appId" -o tsv)
  if [[ -z "$app_id" ]]; then app_id=$(az ad app create --display-name "$APP" --query appId -o tsv); fi
  sp_id=$(az ad sp list --filter "appId eq '$app_id'" --query "[0].id" -o tsv)
  if [[ -z "$sp_id" ]]; then sp_id=$(az ad sp create --id "$app_id" --query id -o tsv); fi

  org_id=$(gh api "orgs/${REPO%%/*}" --jq .id)
  repo_id=$(gh api "repos/$REPO" --jq .id)
  local subjects=("repo:$REPO:environment:$ENV_NAME"
                  "repo:${REPO%%/*}@$org_id/${REPO##*/}@$repo_id:environment:$ENV_NAME")
  local i=0 subject existing
  existing=$(az ad app federated-credential list --id "$app_id" --query "[].subject" -o tsv)
  for subject in "${subjects[@]}"; do
    i=$((i + 1))
    if ! grep -qxF "$subject" <<<"$existing"; then
      az ad app federated-credential create --id "$app_id" -o none --parameters "$(jq -n --arg n "github-aks-$i" \
        --arg s "$subject" '{name: $n, issuer: "https://token.actions.githubusercontent.com", subject: $s,
        audiences: ["api://AzureADTokenExchange"]}')"
    fi
  done

  aks_id=$(azs aks show -g "$RG" -n "$AKS" --query id -o tsv)
  acr_id=$(azs acr show -g "$RG" -n "$ACR" --query id -o tsv)
  assign "$sp_id" ServicePrincipal "Azure Kubernetes Service Cluster User Role" "$aks_id"
  assign "$sp_id" ServicePrincipal "Azure Kubernetes Service RBAC Cluster Admin" "$aks_id"
  assign "$sp_id" ServicePrincipal "AcrPush" "$acr_id"

  local me
  me=$(az ad signed-in-user show --query id -o tsv)
  assign "$me" User "Azure Kubernetes Service RBAC Cluster Admin" "$aks_id"
  echo "identity: $APP (OIDC for $ENV_NAME), roles assigned"
  APP_ID="$app_id"
}

assign() {  # assign <principal-id> <type> <role> <scope>
  local found
  found=$(azs role assignment list --assignee "$1" --role "$3" --scope "$4" --query "length(@)" -o tsv)
  if [[ "$found" == "0" ]]; then
    azs role assignment create --assignee-object-id "$1" --assignee-principal-type "$2" --role "$3" --scope "$4" -o none
  fi
}

github_environment() {
  gh api -X PUT "repos/$REPO/environments/$ENV_NAME" --input - >/dev/null <<<'{
    "deployment_branch_policy": {"protected_branches": true, "custom_branch_policies": false}}'
  local name value
  for name in AZURE_CLIENT_ID AZURE_TENANT_ID AZURE_SUBSCRIPTION_ID AKS_RESOURCE_GROUP AKS_NAME ACR_NAME; do
    case "$name" in
      AZURE_CLIENT_ID) value="$APP_ID" ;;
      AZURE_TENANT_ID) value="$(az account show --subscription "$SUB" --query tenantId -o tsv)" ;;
      AZURE_SUBSCRIPTION_ID) value="$SUB" ;;
      AKS_RESOURCE_GROUP) value="$RG" ;;
      AKS_NAME) value="$AKS" ;;
      ACR_NAME) value="$ACR" ;;
    esac
    gh variable set "$name" --env "$ENV_NAME" --repo "$REPO" --body "$value"
  done
  echo "github: environment $ENV_NAME with its variables"
}

github_secrets() {  # run by the builder: `scripts/azure-setup.sh secrets`
  local name
  for name in OPENAI_API_KEY DASH0_INGEST_TOKEN DASH0_BROWSER_TOKEN; do
    [[ -n "${!name:-}" ]] || { echo "missing $name in the environment" >&2; return 1; }
    printf '%s' "${!name}" | gh secret set "$name" --env "$ENV_NAME" --repo "$REPO"
  done
  echo "github: secrets set for environment $ENV_NAME"
}

main() {
  check_subscription
  if [[ "${1:-}" == "secrets" ]]; then github_secrets; return; fi
  registry
  github_identity
  github_environment
  echo "next (builder): . ~/.config/conferenceops/env.sh && scripts/azure-setup.sh secrets"
}

main "$@"; exit
