#!/usr/bin/env bash
# Configure the ZTWIM platform for the ztwim-openshell-spiffe-tokens demo:
# 1. Discover the cluster domain (used as the SPIRE trust domain)
# 2. Apply the SPIRE custom resources (server, agents, CSI driver, OIDC discovery)
# 3. Verify the platform is ready and print the values the demo needs
#
# Adapted from the openshift-days-ops-showroom ztwim-config-scripts.

set -euo pipefail

NAMESPACE="${ZTWIM_NAMESPACE:-zero-trust-workload-identity-manager}"
CSI_DRIVER_COMPONENT="spiffe-csi-driver"

log() { echo "[ztwim] $*"; }
err() { echo "[ztwim] ERROR: $*" >&2; exit 1; }

oc whoami >/dev/null 2>&1 || err "Not logged into OpenShift"

# --- Discover cluster identity ----------------------------------------------

CLUSTER_DOMAIN="$(oc get ingresses.config/cluster -o jsonpath='{.spec.domain}')"
[[ -n "${CLUSTER_DOMAIN}" ]] || err "Failed to detect cluster domain"
TRUST_DOMAIN="${CLUSTER_DOMAIN}"
CLUSTER_NAME="$(oc get infrastructure cluster -o jsonpath='{.status.infrastructureName}' | cut -d'-' -f1)"
CLUSTER_NAME="${CLUSTER_NAME:-cluster}"
export CLUSTER_DOMAIN TRUST_DOMAIN CLUSTER_NAME

log "Cluster domain: ${CLUSTER_DOMAIN}"
log "Trust domain:   ${TRUST_DOMAIN}"

# --- Apply the SPIRE custom resources ---------------------------------------

log "Applying SPIRE custom resources..."
oc apply -f - <<EOF
apiVersion: operator.openshift.io/v1alpha1
kind: ZeroTrustWorkloadIdentityManager
metadata:
  name: cluster
  namespace: ${NAMESPACE}
spec:
  trustDomain: ${TRUST_DOMAIN}
  clusterName: ${CLUSTER_NAME}
  bundleConfigMap: spire-bundle
---
apiVersion: operator.openshift.io/v1alpha1
kind: SpireServer
metadata:
  name: cluster
  namespace: ${NAMESPACE}
spec:
  caSubject:
    commonName: redhat.com
    country: US
    organization: Red Hat
  persistence:
    size: 5Gi
    accessMode: ReadWriteOnce
  datastore:
    databaseType: sqlite3
    connectionString: /run/spire/data/datastore.sqlite3
  jwtIssuer: https://spire-spiffe-oidc-discovery-provider.${CLUSTER_DOMAIN}
---
apiVersion: operator.openshift.io/v1alpha1
kind: SpireAgent
metadata:
  name: cluster
  namespace: ${NAMESPACE}
spec:
  nodeAttestor:
    k8sPSATEnabled: "true"
  workloadAttestors:
    k8sEnabled: "true"
    workloadAttestorsVerification:
      type: auto
---
apiVersion: operator.openshift.io/v1alpha1
kind: SpiffeCSIDriver
metadata:
  name: cluster
  namespace: ${NAMESPACE}
spec:
  agentSocketPath: /run/spire/agent-sockets
---
apiVersion: operator.openshift.io/v1alpha1
kind: SpireOIDCDiscoveryProvider
metadata:
  name: cluster
  namespace: ${NAMESPACE}
spec:
  jwtIssuer: https://spire-spiffe-oidc-discovery-provider.${CLUSTER_DOMAIN}
  managedRoute: "true"
EOF

# --- Wait for the platform ---------------------------------------------------

daemonset_ready() {
    local component="$1"
    local ready desired
    ready="$(oc get daemonset -n "${NAMESPACE}" -l "app.kubernetes.io/name=${component}" \
        -o jsonpath='{.items[0].status.numberReady}' 2>/dev/null || echo 0)"
    desired="$(oc get daemonset -n "${NAMESPACE}" -l "app.kubernetes.io/name=${component}" \
        -o jsonpath='{.items[0].status.desiredNumberScheduled}' 2>/dev/null || echo 0)"
    [[ "${ready}" -gt 0 ]] && [[ "${ready}" == "${desired}" ]]
}

spire_server_ready() {
    local ready
    ready="$(oc get pods -n "${NAMESPACE}" -l app.kubernetes.io/name=spire-server \
        -o jsonpath='{.items[0].status.containerStatuses[*].ready}' 2>/dev/null || true)"
    [[ "${ready}" == "true true" ]]
}

wait_for() {
    local what="$1" timeout="$2"
    shift 2
    local elapsed=0
    while [[ "${elapsed}" -lt "${timeout}" ]]; do
        if "$@"; then
            log "${what}: ready"
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done
    err "${what} did not become ready within ${timeout}s"
}

wait_for "SPIRE server" 600 spire_server_ready
wait_for "SPIRE agents" 600 daemonset_ready spire-agent
wait_for "SPIFFE CSI driver" 600 daemonset_ready "${CSI_DRIVER_COMPONENT}"

# --- Discovery: values the demo needs ---------------------------------------

AGENT_SOCKET_FILE="$(oc get daemonset -n "${NAMESPACE}" -l app.kubernetes.io/name=spire-agent \
    -o jsonpath='{.items[0].spec.template.spec.containers[0].args}' 2>/dev/null || true)"

log "=== Values for the demo ==="
echo "ZTWIM_NAMESPACE=${NAMESPACE}"
echo "TRUST_DOMAIN=${TRUST_DOMAIN}"
echo "OIDC_ISSUER=https://spire-spiffe-oidc-discovery-provider.${CLUSTER_DOMAIN}"
echo "OIDC_ROUTE=$(oc get route -n "${NAMESPACE}" 2>/dev/null | grep -E 'oidc|NAME' || true)"
echo "SPIRE_AGENT_ARGS=${AGENT_SOCKET_FILE}"

log "Agent socket dir on node (from SpiffeCSIDriver): /run/spire/agent-sockets"
oc get spiffecsidriver cluster -n "${NAMESPACE}" -o jsonpath='{.spec}' 2>/dev/null || true
echo

log "=== SPIRE platform status ==="
oc get pods -n "${NAMESPACE}"
oc get csidriver csi.spiffe.io 2>/dev/null || true

log "ZTWIM platform is ready."
