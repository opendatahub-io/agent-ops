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
namespace_identity() {
    oc get namespace "$NAMESPACE" -o jsonpath='{.metadata.uid}{" "}{.metadata.labels.agent-ops\.opendatahub\.io/managed}'
}
IDENTITY=$(namespace_identity)
NAMESPACE_UID=${IDENTITY%% *}
[[ -n "$NAMESPACE_UID" && "$IDENTITY" == "$NAMESPACE_UID true" ]] || fail 'Namespace is not owned by the deployment helper; use the guide cleanup for a manual installation.'
check_namespace() {
    [[ "$(namespace_identity)" == "$NAMESPACE_UID true" ]] || fail 'Namespace identity or ownership changed; stopping cleanup.'
}
REMOVE_LOCAL=false
if [[ -f "$CONFIG_DIR/agent-ops-namespace-uid" ]] && [[ "$(cat "$CONFIG_DIR/agent-ops-namespace-uid")" == "$NAMESPACE_UID" ]]; then
    command -v openshell >/dev/null || fail 'Install the CLI before cleaning up the recorded local gateway registration.'
    REMOVE_LOCAL=true
fi
if [[ "${1:-}" != --yes ]]; then
    read -r -p "Delete all OpenShell resources in ${NAMESPACE}? [y/N] " reply
    [[ "$reply" == y || "$reply" == Y ]] || exit 0
fi

# Keep the gateway available while the controller deletes workloads normally.
check_namespace
oc delete sandboxes.agents.x-k8s.io --all -n "$NAMESPACE" --wait=true --timeout=180s
check_namespace
helm uninstall openshell -n "$NAMESPACE" --ignore-not-found --wait --timeout=3m
check_namespace
printf '{"apiVersion":"v1","kind":"DeleteOptions","preconditions":{"uid":"%s"}}\n' "$NAMESPACE_UID" |
    oc delete --raw "/api/v1/namespaces/$NAMESPACE" -f -
oc wait --for=delete "namespace/$NAMESPACE" --timeout=180s

# Remove only a local registration that this helper recorded for this namespace UID.
if [[ "$REMOVE_LOCAL" == true ]]; then
    if [[ -f "$CONFIG_DIR/metadata.json" ]]; then
        openshell gateway remove "$GATEWAY_NAME"
    fi
    rm -rf -- "$CONFIG_DIR"
else
    printf 'Local gateway registration retained: no matching helper ownership record.\n'
fi
printf 'Cleanup complete. Agent Sandbox, RuntimeClasses and cluster-wide resources remain installed.\n'
