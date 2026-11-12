#!/usr/bin/env bash
# Clean up an OpenShell namespace created by deploy-openshell.sh.
set -euo pipefail

NAMESPACE="${OPENSHELL_NAMESPACE:-openshell}"
GATEWAY_NAME="${OPENSHELL_GATEWAY_NAME:-openshift}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/openshell/gateways/${GATEWAY_NAME}"
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$NAMESPACE" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ && ${#NAMESPACE} -le 63 ]] || fail 'Invalid namespace.'
case "$NAMESPACE" in default|kube-*|openshift-*|agent-sandbox-system|kubernetes-dashboard) fail 'Cannot remove a system namespace.' ;; esac
[[ "$GATEWAY_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fail 'Invalid gateway name.'
[[ $# -eq 0 || ( $# -eq 1 && "$1" == --yes ) ]] || fail 'Usage: cleanup-openshell.sh [--yes]'
for tool in oc helm; do command -v "$tool" >/dev/null || fail "Missing command: $tool"; done
[[ "$(oc get namespace "$NAMESPACE" -o jsonpath='{.metadata.labels.agent-ops\.opendatahub\.io/managed}')" == true ]] || fail 'Namespace is not owned by the deployment helper; use the guide cleanup for a manual installation.'
NAMESPACE_UID=$(oc get namespace "$NAMESPACE" -o jsonpath='{.metadata.uid}')
[[ -n "$NAMESPACE_UID" ]] || fail 'Could not read namespace identity.'
if [[ "${1:-}" != --yes ]]; then
    read -r -p "Delete all OpenShell resources in ${NAMESPACE}? [y/N] " reply
    [[ "$reply" == y || "$reply" == Y ]] || exit 0
fi

# Keep the gateway available while the controller deletes workloads normally.
oc delete sandboxes.agents.x-k8s.io --all -n "$NAMESPACE" --wait=true --timeout=180s
helm uninstall openshell -n "$NAMESPACE" --wait --timeout=3m
oc delete namespace "$NAMESPACE" --wait=true --timeout=180s

# Remove only a local registration that this helper recorded for this namespace UID.
if [[ -f "$CONFIG_DIR/agent-ops-namespace-uid" ]] && [[ "$(cat "$CONFIG_DIR/agent-ops-namespace-uid")" == "$NAMESPACE_UID" ]]; then
    command -v openshell >/dev/null || fail 'Namespace removed; install the CLI to remove the recorded local gateway registration.'
    openshell gateway remove "$GATEWAY_NAME"
    rm -rf -- "$CONFIG_DIR"
else
    printf 'Local gateway registration retained: no matching helper ownership record.\n'
fi
printf 'Cleanup complete. Agent Sandbox, RuntimeClasses and cluster-wide resources remain installed.\n'
