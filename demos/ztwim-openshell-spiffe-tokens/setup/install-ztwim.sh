#!/usr/bin/env bash
# Install the Zero Trust Workload Identity Manager (ZTWIM) operator from the
# OpenShift OperatorHub catalog (Red Hat Operators - channel stable-v1).
# Same outcome as installing from the console: Ecosystem -> Operators -> OperatorHub.
#
# Upstream: https://github.com/openshift/zero-trust-workload-identity-manager
# Adapted for the ztwim-openshell-spiffe-tokens demo.

set -euo pipefail

NAMESPACE="${ZTWIM_NAMESPACE:-zero-trust-workload-identity-manager}"
CHANNEL="${ZTWIM_CHANNEL:-stable-v1}"
# Package name in the Red Hat Operators catalog (the CSV inside is
# zero-trust-workload-identity-manager.vX.Y.Z).
PACKAGE="${ZTWIM_PACKAGE:-openshift-zero-trust-workload-identity-manager}"
SUBSCRIPTION_NAME="${ZTWIM_SUBSCRIPTION_NAME:-openshift-zero-trust-workload-identity-manager}"
SOURCE="${ZTWIM_CATALOG_SOURCE:-redhat-operators}"
SOURCE_NS="${ZTWIM_CATALOG_NAMESPACE:-openshift-marketplace}"
APPROVAL="${ZTWIM_INSTALL_PLAN_APPROVAL:-Automatic}"

log() { echo "[ztwim] $*"; }

csv_succeeded() {
    local csv
    csv="$(oc get csv -n "${NAMESPACE}" \
        -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null \
        | grep -E 'zero-trust-workload-identity-manager|zerotrust' | head -1)"
    [[ -n "${csv}" ]] || return 1
    [[ "$(oc get csv "${csv}" -n "${NAMESPACE}" -o jsonpath='{.status.phase}')" == "Succeeded" ]]
}

if csv_succeeded; then
    log "Operator already installed (CSV Succeeded)."
    oc get csv -n "${NAMESPACE}" | grep -E 'zero-trust|NAME'
    exit 0
fi

log "Creating namespace ${NAMESPACE} (if needed)..."
oc create namespace "${NAMESPACE}" --dry-run=client -o yaml | oc apply -f -

if ! oc get operatorgroup zero-trust-workload-identity-manager -n "${NAMESPACE}" >/dev/null 2>&1; then
    log "Creating OperatorGroup..."
    oc apply -f - <<EOF
apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: zero-trust-workload-identity-manager
  namespace: ${NAMESPACE}
spec: {}
EOF
fi

if ! oc get subscription "${SUBSCRIPTION_NAME}" -n "${NAMESPACE}" >/dev/null 2>&1; then
    log "Subscribing to ${PACKAGE} (${CHANNEL}) from ${SOURCE}..."
    oc apply -f - <<EOF
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: ${SUBSCRIPTION_NAME}
  namespace: ${NAMESPACE}
spec:
  channel: ${CHANNEL}
  installPlanApproval: ${APPROVAL}
  name: ${PACKAGE}
  source: ${SOURCE}
  sourceNamespace: ${SOURCE_NS}
EOF
fi

log "Waiting for operator CSV to reach Succeeded (up to 15 minutes)..."
elapsed=0
while [[ "${elapsed}" -lt 900 ]]; do
    if csv_succeeded; then
        log "Operator installed successfully."
        oc get csv -n "${NAMESPACE}" | grep -E 'zero-trust|NAME'
        exit 0
    fi
    sleep 15
    elapsed=$((elapsed + 15))
    if [[ $((elapsed % 60)) -eq 0 ]]; then
        log "  still waiting (${elapsed}s)..."
    fi
done

echo "[ztwim] ERROR: Timed out. Check: oc get subscription,csv,installplans.operators.coreos.com -n ${NAMESPACE}" >&2
exit 1
