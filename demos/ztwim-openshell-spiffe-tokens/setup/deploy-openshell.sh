#!/usr/bin/env bash
# Deploy OpenShell v0.1.6 on OpenShift with ZTWIM-provided SPIRE identities.
#
# - Upstream NVIDIA chart (the SPIFFE provider token grant features shipped in
#   OpenShell v0.1.0; the Red Hat build v0.0.116 predates them).
# - Capability-free sandbox workloads: the Kubernetes driver reads the
#   namespace's openshift.io/sa.scc.uid-range annotation - no privileged SCC
#   and no securityContext overrides on OpenShift 4.19+.
# - providerTokenGrants.spiffe mounts the SPIFFE Workload API socket into the
#   gateway and sandbox pods through the csi.spiffe.io CSI driver provided by
#   ZTWIM - OpenShell runs on ZTWIM's SPIRE, not its own.
# - disableTls + allowUnauthenticatedUsers for the demo: the gateway is
#   reached through a local port-forward, never through an exposed Route.
#
# Adapted from the OpenShell OpenShift guide (docs/kubernetes/openshift.mdx).

set -euo pipefail

NAMESPACE="${OPENSHELL_NAMESPACE:-openshell}"
RELEASE="${OPENSHELL_RELEASE:-openshell}"
CHART_VERSION="${OPENSHELL_CHART_VERSION:-0.1.6}"
CHART="${OPENSHELL_CHART:-oci://ghcr.io/nvidia/openshell/helm-chart}"
# Path to ZTWIM's SPIFFE Workload API socket inside gateway/sandbox pods.
# ZTWIM's SpiffeCSIDriver serves the node agent socket through csi.spiffe.io.
SOCKET_PATH="${OPENSHELL_SPIFFE_SOCKET_PATH:-/run/spire/sockets/spire-agent.sock}"

log() { echo "[openshell] $*"; }
err() { echo "[openshell] ERROR: $*" >&2; exit 1; }

oc whoami >/dev/null 2>&1 || err "Not logged into OpenShift"

# --- Prerequisites -----------------------------------------------------------

oc get crd sandboxes.agents.x-k8s.io >/dev/null 2>&1 \
    || err "Agent Sandbox CRDs not found - install the Agent Sandbox operator first"
oc get csidriver csi.spiffe.io >/dev/null 2>&1 \
    || err "csi.spiffe.io CSIDriver not found - install and configure ZTWIM first"

if helm status "${RELEASE}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    log "Release ${RELEASE} already installed in ${NAMESPACE}."
    helm get values "${RELEASE}" -n "${NAMESPACE}"
    exit 0
fi

# --- Install -----------------------------------------------------------------

log "Creating namespace ${NAMESPACE}..."
oc create namespace "${NAMESPACE}" --dry-run=client -o yaml | oc apply -f -

log "Installing OpenShell ${CHART_VERSION} (${CHART})..."
log "  SPIFFE Workload API socket: ${SOCKET_PATH} (via ZTWIM's csi.spiffe.io)"
helm install "${RELEASE}" "${CHART}" \
    --version "${CHART_VERSION}" \
    --namespace "${NAMESPACE}" \
    --set podSecurityContext.fsGroup=null \
    --set securityContext.runAsUser=null \
    --set server.disableTls=true \
    --set server.auth.allowUnauthenticatedUsers=true \
    --set server.providerTokenGrants.spiffe.enabled=true \
    --set "server.providerTokenGrants.spiffe.workloadApiSocketPath=${SOCKET_PATH}"

log "Waiting for the gateway..."
oc -n "${NAMESPACE}" rollout status statefulset/openshell --timeout=300s \
    || oc -n "${NAMESPACE}" rollout status deployment/openshell --timeout=300s

log "=== OpenShell status ==="
oc -n "${NAMESPACE}" get pods
oc -n "${NAMESPACE}" get svc

log "OpenShell deployed. Connect locally with:"
echo "  oc -n ${NAMESPACE} port-forward svc/${RELEASE} 8080:8080"
echo "  openshell gateway add http://127.0.0.1:8080 --local --name openshift"
