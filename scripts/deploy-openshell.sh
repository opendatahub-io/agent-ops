#!/usr/bin/env bash
# Install OpenShell 0.1.7 on OpenShift using ODH runtime images.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="${OPENSHELL_NAMESPACE:-openshell}"
GATEWAY_NAME="${OPENSHELL_GATEWAY_NAME:-openshift}"
HELM_VERSION="${OPENSHELL_HELM_VERSION:-0.1.7}"
VALUES_FILE="${SCRIPT_DIR}/../examples/openshift/values-odh.yaml"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/openshell/gateways/${GATEWAY_NAME}"
OWNER_LABEL="agent-ops.opendatahub.io/managed"

fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$NAMESPACE" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ && ${#NAMESPACE} -le 63 ]] || fail 'Invalid namespace.'
case "$NAMESPACE" in default|kube-*|openshift-*|agent-sandbox-system|kubernetes-dashboard) fail 'Choose an unused application namespace.' ;; esac
[[ "$GATEWAY_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || fail 'Invalid gateway name.'
[[ "$HELM_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'Invalid chart version.'
[[ -r "$VALUES_FILE" ]] || fail "Shared Helm values not found: $VALUES_FILE"
for tool in oc helm openshell base64 jq; do command -v "$tool" >/dev/null || fail "Missing command: $tool"; done
CLI_VERSION=$(openshell --version)
[[ "$CLI_VERSION" == "openshell ${HELM_VERSION}" || "$CLI_VERSION" == "openshell ${HELM_VERSION}-"* ]] || fail "CLI/chart mismatch: ${CLI_VERSION}; install the matching CLI with ${SCRIPT_DIR}/install-openshell-cli.sh."
[[ ! -e "$CONFIG_DIR" ]] || fail "Gateway configuration already exists: ${GATEWAY_NAME}. Choose OPENSHELL_GATEWAY_NAME without overwriting it."
oc cluster-info >/dev/null
oc get crd sandboxes.agents.x-k8s.io >/dev/null || fail 'Install Red Hat build of Agent Sandbox 0.9.0 via Software Catalog first.'
DOMAIN=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')
[[ -n "$DOMAIN" ]] || fail 'Cluster ingress domain is empty.'
ROUTE_HOST="openshell-${NAMESPACE}.${DOMAIN}"

if [[ -n "$(oc get namespace "$NAMESPACE" --ignore-not-found -o name)" ]]; then
    [[ "$(oc get namespace "$NAMESPACE" -o jsonpath='{.metadata.labels.agent-ops\.opendatahub\.io/managed}')" == true ]] || fail 'Namespace exists and is not owned by this helper.'
else
    oc create namespace "$NAMESPACE"
    oc label namespace "$NAMESPACE" "${OWNER_LABEL}=true"
fi
NAMESPACE_UID=$(oc get namespace "$NAMESPACE" -o jsonpath='{.metadata.uid}')
[[ -n "$NAMESPACE_UID" ]] || fail 'Could not read namespace identity.'

helm_args=(
    upgrade --install openshell oci://ghcr.io/nvidia/openshell/helm-chart
    --version "$HELM_VERSION" --namespace "$NAMESPACE"
    -f "$VALUES_FILE"
    --set "pkiInitJob.serverDnsNames[0]=$ROUTE_HOST"
    --wait --timeout 5m
)

# Optional environment overrides take precedence over the shared defaults.
if [[ -n "${ODH_IMAGE_TAG:-}" ]]; then
    for component in gateway supervisor sandboxRuntime; do
        helm_args+=(--set-string "$component.image.tag=$ODH_IMAGE_TAG")
    done
fi
add_image_overrides() {
    local component="$1" repository="$2" tag="$3"
    if [[ -n "$repository" ]]; then
        [[ "$repository" == */* ]] || fail 'Image repositories must include their registry.'
        helm_args+=(
            --set-string "$component.image.registry=${repository%%/*}"
            --set-string "$component.image.repository=${repository#*/}"
        )
    fi
    if [[ -n "$tag" ]]; then
        helm_args+=(--set-string "$component.image.tag=$tag")
    fi
}
add_image_overrides gateway "${OPENSHELL_GATEWAY_IMAGE_REPOSITORY:-}" "${OPENSHELL_GATEWAY_IMAGE_TAG:-}"
add_image_overrides supervisor "${OPENSHELL_SUPERVISOR_IMAGE_REPOSITORY:-}" "${OPENSHELL_SUPERVISOR_IMAGE_TAG:-}"
add_image_overrides sandboxRuntime "${OPENSHELL_SANDBOX_RUNTIME_IMAGE_REPOSITORY:-}" "${OPENSHELL_SANDBOX_RUNTIME_IMAGE_TAG:-}"
helm "${helm_args[@]}"

oc create route passthrough openshell --service=openshell --port=8080 \
    --hostname="$ROUTE_HOST" -n "$NAMESPACE" --dry-run=client -o yaml | oc apply -f -

umask 077
mkdir -p "$(dirname "$CONFIG_DIR")"
STAGING_DIR=$(mktemp -d "$(dirname "$CONFIG_DIR")/.${GATEWAY_NAME}.XXXXXX")
trap 'rm -rf -- "$STAGING_DIR"' EXIT
mkdir -p "$STAGING_DIR/gateway/mtls"
for key in ca.crt tls.crt tls.key; do
    oc get secret openshell-client-tls -n "$NAMESPACE" -o "jsonpath={.data.${key//./\\.}}" |
        base64 -d > "$STAGING_DIR/gateway/mtls/$key"
    [[ -s "$STAGING_DIR/gateway/mtls/$key" ]] || fail "Missing TLS material: $key"
done
printf '%s\n' "$NAMESPACE_UID" > "$STAGING_DIR/gateway/agent-ops-namespace-uid"
cp -R "$STAGING_DIR/gateway/mtls" "$STAGING_DIR/mtls"
mv "$STAGING_DIR/gateway" "$CONFIG_DIR"
# Registration may import a package-managed bundle; restore this cluster's bundle afterward.
openshell gateway add "https://${ROUTE_HOST}" --local --name "$GATEWAY_NAME"
cp "$STAGING_DIR/mtls/"* "$CONFIG_DIR/mtls/"
for attempt in {1..15}; do
    if status=$(openshell status --gateway "$GATEWAY_NAME" --output json) &&
        jq -e '.status == "connected" and .authentication.status == "authenticated"' <<< "$status" >/dev/null; then
        printf '%s\n' "$status"
        printf 'OpenShell is ready. Use gateway %s and namespace %s.\n' "$GATEWAY_NAME" "$NAMESPACE"
        exit 0
    fi
    sleep 2
done
printf '%s\n' "$status" >&2
fail 'Gateway did not become connected and authenticated over gRPC. Inspect the Route and TLS configuration.'
