#!/bin/bash

# Deploy the RedBank Playground UI to OpenShift via Helm.

SCRIPT_FOLDER="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

function _out() {
  echo "$(date +'%F %H:%M:%S') $@"
}

function setup() {
  local ns="${NAMESPACE:-redbank-demo}"
  local agent_name="redbank-playground"

  # Derive orchestrator URL from namespace if not explicitly set
  local orch_url="${ORCHESTRATOR_URL:-http://redbank-orchestrator.${ns}.svc:8080}"

  _out "Deploying ${agent_name} to namespace: ${ns}"

  oc new-project "${ns}" 2>/dev/null || oc project "${ns}"

  cd "${SCRIPT_FOLDER}"

  _out "Building playground image"
  oc new-build --strategy=docker --binary --name="${agent_name}" \
    --to="${agent_name}:latest" --namespace "${ns}" 2>/dev/null || true
  oc start-build "${agent_name}" --from-dir=. --follow --namespace "${ns}"

  local img="image-registry.openshift-image-registry.svc:5000/${ns}/${agent_name}:latest"
  local image_repo="${img%:*}"
  local image_tag="latest"

  _out "Deploying playground via Helm"

  local helm_sets=(
    --set image.repository="${image_repo}"
    --set image.tag="${image_tag}"
    --set env.ORCHESTRATOR_URL="${orch_url}"
  )

  [[ -n "${KEYCLOAK_URL:-}" ]] && helm_sets+=(--set env.KEYCLOAK_URL="${KEYCLOAK_URL}")
  [[ -n "${KEYCLOAK_REALM:-}" ]] && helm_sets+=(--set env.KEYCLOAK_REALM="${KEYCLOAK_REALM}")
  [[ -n "${KEYCLOAK_CLIENT_ID:-}" ]] && helm_sets+=(--set env.KEYCLOAK_CLIENT_ID="${KEYCLOAK_CLIENT_ID}")

  helm upgrade --install "${agent_name}" ./charts/agent \
    --namespace "${ns}" \
    -f values.yaml \
    "${helm_sets[@]}"

  _out "Waiting for rollout..."
  if oc rollout status "deployment/${agent_name}" --namespace "${ns}" --timeout=120s; then
    local route
    route=$(oc get route "${agent_name}" --namespace "${ns}" -o jsonpath='{.spec.host}' 2>/dev/null || true)
    if [[ -n "${route}" ]]; then
      _out "Playground available at: https://${route}"
    fi
    if [[ -n "${KEYCLOAK_URL:-}${KEYCLOAK_REALM:-}${KEYCLOAK_CLIENT_ID:-}" ]]; then
      _out "Registering Keycloak redirect URI..."
      NAMESPACE="${ns}" bash scripts/register-keycloak-redirect.sh || \
        _out "WARNING: Keycloak redirect registration failed."
    fi
  else
    _out "WARNING: Rollout did not complete. Check with:"
    echo "  oc get pods -n ${ns} -l app.kubernetes.io/name=${agent_name}"
    echo "  oc logs -n ${ns} deployment/${agent_name}"
  fi

  _out "Done deploying ${agent_name}"
}

setup
