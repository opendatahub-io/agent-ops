#!/bin/bash
#
# Tear down RedBank demo workloads. Keeps the namespace and build configs.

set -euo pipefail

NAMESPACE="${NAMESPACE:-redbank-demo}"

function _out() {
  echo "$(date +'%F %H:%M:%S') $@"
}

_out "Cleaning up RedBank workloads in namespace: ${NAMESPACE}"
oc project "${NAMESPACE}"

_out "Deleting AgentRuntime CRs"
oc delete agentruntime redbank-banking-agent-runtime --ignore-not-found
oc delete agentruntime redbank-knowledge-agent-runtime --ignore-not-found
oc delete agentruntime redbank-mcp-server-runtime --ignore-not-found
oc delete agentruntime redbank-orchestrator-runtime --ignore-not-found

_out "Deleting Knowledge Agent deployment and service"
oc delete deployment redbank-knowledge-agent --ignore-not-found
oc delete service redbank-knowledge-agent --ignore-not-found

_out "Deleting Banking Agent deployment and service"
oc delete deployment redbank-banking-agent --ignore-not-found
oc delete service redbank-banking-agent --ignore-not-found

_out "Deleting MCP server deployment and service"
oc delete deployment redbank-mcp-server --ignore-not-found
oc delete service redbank-mcp-server --ignore-not-found

_out "Deleting Orchestrator (Helm release)"
helm uninstall redbank-orchestrator --ignore-not-found 2>/dev/null || true

_out "Deleting Playground (Helm release)"
helm uninstall redbank-playground --ignore-not-found 2>/dev/null || true

_out "Deleting PostgreSQL deployment and service"
oc delete deployment postgresql --ignore-not-found
oc delete service postgresql --ignore-not-found

_out "Deleting PersistentVolumeClaim"
oc delete pvc postgres-pvc --ignore-not-found

_out "Deleting secrets and configmaps"
oc delete secret postgresql-credentials --ignore-not-found
oc delete secret pgvector-credentials --ignore-not-found
oc delete configmap -l app=postgresql --ignore-not-found

_out "Cleaning up Keycloak realm"
if [[ -z "${KEYCLOAK_URL:-}" ]]; then
  KEYCLOAK_URL="https://$(oc get route keycloak -n keycloak -o jsonpath='{.spec.host}' 2>/dev/null)" || true
fi
if [[ -n "${KEYCLOAK_URL}" && "${KEYCLOAK_URL}" != "https://" && -n "${KEYCLOAK_ADMIN:-}" && -n "${KEYCLOAK_PASSWORD:-}" ]]; then
  TOKEN=$(curl -sf "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
    -d "grant_type=password" -d "client_id=admin-cli" \
    -d "username=${KEYCLOAK_ADMIN}" -d "password=${KEYCLOAK_PASSWORD}" | jq -r '.access_token' 2>/dev/null) || true
  if [[ -n "${TOKEN}" && "${TOKEN}" != "null" ]]; then
    curl -sf -X DELETE "${KEYCLOAK_URL}/admin/realms/redbank" \
      -H "Authorization: Bearer ${TOKEN}" 2>/dev/null && \
      _out "Deleted Keycloak realm 'redbank'" || \
      _out "Keycloak realm 'redbank' not found or already deleted"
  else
    _out "WARNING: Could not authenticate to Keycloak — skipping realm cleanup"
  fi
else
  _out "WARNING: KEYCLOAK_URL/KEYCLOAK_ADMIN/KEYCLOAK_PASSWORD not set — skipping Keycloak cleanup"
fi

_out "Cleanup complete — namespace '${NAMESPACE}' and build configs retained"
