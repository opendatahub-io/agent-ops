#!/usr/bin/env bash

# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Remove the demo resources. Pass --all to also uninstall OpenShell and ZTWIM
# (the demo workloads, sandbox, provider and profile are removed by default).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOVE_ALL="${1:-}"
WORKLOAD_NAMESPACE="${WORKLOAD_NAMESPACE:-default}"

log() { echo "[cleanup] $*"; }

: "${KUBECONFIG:?KUBECONFIG must point at the target cluster}"

# Sandbox / provider / profile (through the gateway port-forward)
export XDG_CONFIG_HOME="${XDG_CONFIG_HOME:-$(mktemp -d)}"
oc -n openshell port-forward svc/openshell 8097:8080 >/dev/null 2>&1 &
PF_PID=$!
for _ in $(seq 1 40); do nc -z 127.0.0.1 8097 >/dev/null 2>&1 && break; sleep 0.25; done

for name in spiffe-token-demo spiffe-exchange; do
    openshell --gateway-endpoint http://127.0.0.1:8097 sandbox delete "$name" >/dev/null 2>&1 || true
done
for name in spiffe-token-demo spiffe-token-exchange-demo; do
    openshell --gateway-endpoint http://127.0.0.1:8097 provider delete "$name" >/dev/null 2>&1 || true
    openshell --gateway-endpoint http://127.0.0.1:8097 profile delete "$name" >/dev/null 2>&1 || true
done
kill "$PF_PID" >/dev/null 2>&1 || true

log "Removing the demo workloads..."
kubectl -n "$WORKLOAD_NAMESPACE" delete -k "${SCRIPT_DIR}/../k8s/token-grant" --ignore-not-found --timeout=120s
kubectl -n "$WORKLOAD_NAMESPACE" delete -k "${SCRIPT_DIR}/../k8s/token-exchange" --ignore-not-found --timeout=120s
kubectl -n "$WORKLOAD_NAMESPACE" delete secret openshell-spiffe-token-demo openshell-spiffe-token-exchange-demo --ignore-not-found

if [[ "${REMOVE_ALL}" == "--all" ]]; then
    log "Uninstalling OpenShell..."
    helm uninstall openshell -n openshell --ignore-not-found || true
    oc delete namespace openshell --ignore-not-found --timeout=120s || true

    log "Removing the ZTWIM SPIRE custom resources..."
    oc delete zerotrustworkloadidentitymanager,spireserver,spireagent,spiffecsidriver,spireoidcdiscoveryprovider \
        cluster -n zero-trust-workload-identity-manager --ignore-not-found --timeout=180s || true

    log "Removing the ZTWIM operator..."
    oc delete subscription openshift-zero-trust-workload-identity-manager \
        -n zero-trust-workload-identity-manager --ignore-not-found --timeout=120s || true
    oc delete csv -n zero-trust-workload-identity-manager \
        -l "operators.coreos.com/openshift-zero-trust-workload-identity-manager.zero-trust-workload-identity-manager" \
        --ignore-not-found --timeout=180s || true
    oc delete operatorgroup zero-trust-workload-identity-manager \
        -n zero-trust-workload-identity-manager --ignore-not-found || true
    oc delete namespace zero-trust-workload-identity-manager --ignore-not-found --timeout=180s || true
fi

log "Cleanup done."
