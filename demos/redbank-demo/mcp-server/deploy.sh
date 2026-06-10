#!/bin/bash

# Deploy the RedBank MCP server to OpenShift.

SCRIPT_FOLDER="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"

function _out() {
  echo "$(date +'%F %H:%M:%S') $@"
}

function setup() {
  local ns="${NAMESPACE:-redbank-demo}"
  _out "Deploying redbank-mcp-server to namespace: ${ns}"

  if [[ -z "${KEYCLOAK_HOST:-}" ]]; then
    KEYCLOAK_HOST=$(oc get route keycloak -n keycloak -o jsonpath='{.spec.host}' 2>/dev/null) || true
  fi
  if [[ -z "${KEYCLOAK_HOST}" ]]; then
    echo "ERROR: KEYCLOAK_HOST not set and could not auto-detect from 'oc get route keycloak -n keycloak'." >&2
    echo "Set KEYCLOAK_HOST=<your-keycloak-host> and re-run." >&2
    exit 1
  fi
  _out "Using Keycloak host: ${KEYCLOAK_HOST}"

  oc new-project "${ns}" 2>/dev/null || oc project "${ns}"

  _out "Creating/updating pgvector-credentials secret"
  oc create secret generic pgvector-credentials \
    --from-literal=PGVECTOR_USER="${PGVECTOR_USER:-app}" \
    --from-literal=PGVECTOR_PASSWORD="${PGVECTOR_PASSWORD:-app}" \
    --dry-run=client -o yaml | oc apply -f -

  cd "${SCRIPT_FOLDER}"

  _out Building MCP server image
  oc new-build --name build-redbank-mcp-server --binary --strategy docker \
    --to="image-registry.openshift-image-registry.svc:5000/${ns}/redbank-mcp-server:latest" 2>/dev/null || true
  oc start-build build-redbank-mcp-server --from-dir=. --follow

  _out Deploying MCP server
  NAMESPACE="${ns}" KEYCLOAK_HOST="${KEYCLOAK_HOST}" \
    envsubst '${NAMESPACE} ${KEYCLOAK_HOST}' < ./mcp-server.yaml | oc apply -f -

  _out Applying AgentRuntime CR
  oc apply -f ./agentruntime.yaml

  _out Done deploying redbank-mcp-server
}

setup
