#!/usr/bin/env bash
# OpenShell deployment for OpenShift, pinned to the build validated on OCP 4.20:
# upstream main @ 8719fc9 (chart 0.0.0-dev.8719fc9..., images tagged with the same commit)
# Based on opendatahub-io/agent-ops pinned version testing

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Configuration with defaults
NAMESPACE="${OPENSHELL_NAMESPACE:-openshell}"
GATEWAY_NAME="${OPENSHELL_GATEWAY_NAME:-openshift}"
# Validated build: Red Hat (ODH) images v0.1.2-rhaiv.5, built from upstream main @ e7fdd6b,
# with the upstream development chart for that same commit.
HELM_VERSION="${OPENSHELL_HELM_VERSION:-0.0.0-dev.e7fdd6beef98f7f92d86271a169fdd4d3be44cf3}"
# Product images (quay.io/opendatahub/odh-openshell-*). Set ODH_IMAGE_TAG= (empty) to use the
# upstream images the chart selects instead (ghcr.io/nvidia/openshell/*:<chart commit>).
ODH_IMAGE_TAG="${ODH_IMAGE_TAG-v0.1.2-rhaiv.5}"
ODH_IMAGE_REGISTRY="${ODH_IMAGE_REGISTRY:-quay.io/opendatahub}"
# Repository name prefix under ODH_IMAGE_REGISTRY: <prefix>{gateway,supervisor,sandbox}.
# Set to "openshell-" for custom builds such as quay.io/<you>/openshell-gateway.
ODH_IMAGE_REPO_PREFIX="${ODH_IMAGE_REPO_PREFIX:-odh-openshell-}"
# Set to true to mount the SPIFFE Workload API (ZTWIM) for dynamic provider token grants.
ENABLE_SPIFFE="${OPENSHELL_ENABLE_SPIFFE:-false}"
# User authentication. Empty issuer: no user login, every caller is a trusted local developer
# (evaluation only). With an issuer, the gateway validates OIDC tokens and refuses anonymous callers.
OIDC_ISSUER="${OPENSHELL_OIDC_ISSUER:-}"
OIDC_AUDIENCE="${OPENSHELL_OIDC_AUDIENCE:-openshell-gateway}"
OIDC_ROLES_CLAIM="${OPENSHELL_OIDC_ROLES_CLAIM:-realm_access.roles}"
OIDC_ADMIN_ROLE="${OPENSHELL_OIDC_ADMIN_ROLE:-openshell-admin}"
OIDC_USER_ROLE="${OPENSHELL_OIDC_USER_ROLE:-openshell-user}"
# ConfigMap (key ca.crt) with the CA that signs the issuer, when it is not publicly trusted.
OIDC_CA_CONFIGMAP="${OPENSHELL_OIDC_CA_CONFIGMAP:-}"

# Validate inputs to prevent injection attacks
if [[ ! "$NAMESPACE" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]; then
    echo "ERROR: Invalid namespace name. Must match Kubernetes naming conventions." >&2
    exit 1
fi

# Enforce Kubernetes DNS label length limit (63 characters)
if [[ ${#NAMESPACE} -gt 63 ]]; then
    echo "ERROR: Namespace name too long (${#NAMESPACE} chars). Maximum is 63 characters." >&2
    exit 1
fi

# Reject protected OpenShift/Kubernetes system namespaces
case "$NAMESPACE" in
    default|kube-*|openshift-*|kubernetes-dashboard)
        echo "ERROR: Cannot use protected system namespace: $NAMESPACE" >&2
        echo "This script only manages namespaces it creates." >&2
        exit 1
        ;;
esac

if [[ ! "$GATEWAY_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo "ERROR: Invalid gateway name. Must be alphanumeric with dots, underscores, or hyphens." >&2
    exit 1
fi

if [[ ! "$HELM_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$ ]]; then
    echo "ERROR: Invalid Helm version. Must be semver, optionally with a pre-release (e.g., 0.0.0-dev.<commit>)." >&2
    exit 1
fi

if [[ ! "$ODH_IMAGE_REPO_PREFIX" =~ ^[a-z0-9._/-]*$ || ! "$ODH_IMAGE_REGISTRY" =~ ^[A-Za-z0-9.:/_-]+$ ]]; then
    echo "ERROR: Invalid ODH_IMAGE_REGISTRY or ODH_IMAGE_REPO_PREFIX." >&2
    exit 1
fi

if [[ -n "$OIDC_ISSUER" && ! "$OIDC_ISSUER" =~ ^https://[A-Za-z0-9.:/_-]+$ ]]; then
    echo "ERROR: OPENSHELL_OIDC_ISSUER must be an https:// URL (the gateway rejects HTTP issuers)." >&2
    exit 1
fi
if [[ -n "$OIDC_CA_CONFIGMAP" && ! "$OIDC_CA_CONFIGMAP" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]]; then
    echo "ERROR: Invalid OPENSHELL_OIDC_CA_CONFIGMAP." >&2
    exit 1
fi
for v in "$OIDC_AUDIENCE" "$OIDC_ROLES_CLAIM" "$OIDC_ADMIN_ROLE" "$OIDC_USER_ROLE"; do
    if [[ ! "$v" =~ ^[A-Za-z0-9._:/-]+$ ]]; then
        echo "ERROR: Invalid OIDC setting: $v" >&2
        exit 1
    fi
done

if [[ -n "$ODH_IMAGE_TAG" && ! "$ODH_IMAGE_TAG" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    echo "ERROR: Invalid ODH_IMAGE_TAG. Must be an image tag such as v0.1.2-rhaiv.5." >&2
    exit 1
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*"
}

log_step() {
    echo -e "${BLUE}==>${NC} $*"
}

# Check prerequisites
check_prerequisites() {
    log_step "Checking prerequisites..."

    # Check oc
    if ! command -v oc &> /dev/null; then
        log_error "OpenShift CLI (oc) is not installed. Please install it and try again."
        exit 1
    fi
    log_info "Using OpenShift CLI (oc)"

    # Check helm
    if ! command -v helm &> /dev/null; then
        log_error "Helm is not installed. Please install it and try again."
        exit 1
    fi

    # Check openshell CLI
    if ! command -v openshell &> /dev/null; then
        log_error "OpenShell CLI is not installed. Install with:"
        log_error "  ${SCRIPT_DIR}/install-openshell-cli.sh"
        exit 1
    fi
    log_info "OpenShell CLI: $(openshell --version 2>&1 | head -1 || echo 'installed')"

    # Check cluster connectivity
    if ! oc cluster-info &> /dev/null; then
        log_error "Cannot connect to cluster. Please check your kubeconfig."
        exit 1
    fi

    log_info "Cluster: $(oc cluster-info | head -1)"
}

# Check Agent Sandbox CRD
check_agent_sandbox() {
    log_step "Checking for Agent Sandbox CRD..."

    if oc get crd sandboxes.agents.x-k8s.io &> /dev/null; then
        log_info "Agent Sandbox CRD is installed ✓"

        # Check if it's the Red Hat build
        if oc get deployment -n agent-sandbox-system agent-sandbox-controller &> /dev/null; then
            local image=$(oc get deployment -n agent-sandbox-system agent-sandbox-controller -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo "unknown")
            log_info "Controller image: ${image}"
        fi
        return 0
    else
        log_error "Agent Sandbox CRD not found!"
        log_error ""
        log_error "RECOMMENDED: Install the Red Hat build of Agent Sandbox v0.9.0 from:"
        log_error "  OpenShift Console → OperatorHub → Search for 'Agent Sandbox'"
        log_error ""
        log_error "Alternatively, install a specific pinned upstream version (NOT 'latest'):"
        log_error "  # Example for v0.9.0 (pin to a specific version):"
        log_error "  kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v0.9.0/sandbox-with-extensions.yaml"
        log_error ""
        log_error "WARNING: Never use 'releases/latest' - always pin to a specific version tag"
        log_error ""
        exit 1
    fi
}

# Create namespace and setup permissions
setup_namespace() {
    log_step "Setting up namespace..."

    # Create namespace
    oc create namespace "${NAMESPACE}" --dry-run=client -o yaml | oc apply -f -
    log_info "Namespace ${NAMESPACE} created/verified"

    # OpenShell 0.1.x is capability-free: sandbox, supervisor, and gateway pods run
    # under the default restricted-v2 SCC with UIDs from the namespace range. No
    # privileged SCC grant is needed.

    # Add SCC for certgen job
    oc adm policy add-scc-to-user restricted-v2 -z openshell-certgen -n "${NAMESPACE}" 2>/dev/null || true
}

# Install OpenShell via Helm
install_helm() {
    log_step "Installing OpenShell via Helm..."

    # Get the ingress domain
    INGRESS_DOMAIN=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}' 2>/dev/null || true)
    if [[ -z "${INGRESS_DOMAIN}" ]]; then
        log_error "Could not detect OpenShift ingress domain"
        exit 1
    fi

    ROUTE_HOSTNAME="openshell-${NAMESPACE}.${INGRESS_DOMAIN}"
    log_info "Route hostname for certificate SANs: ${ROUTE_HOSTNAME}"

    local helm_args=(
        --version "${HELM_VERSION}"
        --namespace "${NAMESPACE}"
        --set podSecurityContext.fsGroup=null
        --set securityContext.runAsUser=null
        --set "pkiInitJob.serverDnsNames[0]=${ROUTE_HOSTNAME}"
    )
    if [[ -n "${OIDC_ISSUER}" ]]; then
        helm_args+=(
            --set server.auth.allowUnauthenticatedUsers=false
            --set server.oidc.issuer="${OIDC_ISSUER}"
            --set server.oidc.audience="${OIDC_AUDIENCE}"
            --set server.oidc.rolesClaim="${OIDC_ROLES_CLAIM}"
            --set server.oidc.adminRole="${OIDC_ADMIN_ROLE}"
            --set server.oidc.userRole="${OIDC_USER_ROLE}"
        )
        [[ -n "${OIDC_CA_CONFIGMAP}" ]] && helm_args+=(--set server.oidc.caConfigMapName="${OIDC_CA_CONFIGMAP}")
    else
        helm_args+=(--set server.auth.allowUnauthenticatedUsers=true)
    fi
    if [[ -n "${ODH_IMAGE_TAG}" ]]; then
        helm_args+=(
            --set global.image.registry="${ODH_IMAGE_REGISTRY}"
            --set global.image.tag="${ODH_IMAGE_TAG}"
            --set gateway.image.repository="${ODH_IMAGE_REPO_PREFIX}gateway"
            --set supervisor.image.repository="${ODH_IMAGE_REPO_PREFIX}supervisor"
            --set sandboxRuntime.image.repository="${ODH_IMAGE_REPO_PREFIX}sandbox"
        )
    fi
    if [[ "${ENABLE_SPIFFE}" == "true" ]]; then
        helm_args+=(--set server.providerTokenGrants.spiffe.enabled=true)
    fi

    if helm list -n "${NAMESPACE}" | grep -q "^openshell"; then
        log_info "OpenShell already installed, upgrading..."
        # Helm 4 applies server-side. scripts/token-exchange/05-deploy-registrar.sh edits the
        # gateway ConfigMap and StatefulSet, so take back ownership; step 5 must then be re-run.
        if helm upgrade --help 2>/dev/null | grep -q -- '--force-conflicts'; then
            helm_args+=(--force-conflicts)
        fi
        helm upgrade openshell oci://ghcr.io/nvidia/openshell/helm-chart "${helm_args[@]}"
        if oc -n "${NAMESPACE}" get deploy keycloak-registrar &> /dev/null; then
            log_warn "Helm reset the gateway's interceptor registration."
            log_warn "Re-run scripts/token-exchange/05-deploy-registrar.sh (make token-exchange does this)."
        fi
    else
        log_info "Installing OpenShell ${HELM_VERSION}..."
        helm install openshell oci://ghcr.io/nvidia/openshell/helm-chart "${helm_args[@]}"
    fi

    log_info "OpenShell installed successfully"
}

# Wait for deployment
wait_for_deployment() {
    log_step "Waiting for OpenShell to be ready..."

    # A StatefulSet does not replace a pod that is not Ready, so a gateway crash-looping on the
    # previous configuration (for example an unreachable OIDC issuer) would never pick up the fix.
    local pod_rev update_rev ready
    pod_rev=$(oc -n "${NAMESPACE}" get pod openshell-0 -o jsonpath='{.metadata.labels.controller-revision-hash}' 2>/dev/null || true)
    update_rev=$(oc -n "${NAMESPACE}" get statefulset openshell -o jsonpath='{.status.updateRevision}' 2>/dev/null || true)
    ready=$(oc -n "${NAMESPACE}" get pod openshell-0 -o jsonpath='{.status.containerStatuses[0].ready}' 2>/dev/null || true)
    if [[ -n "${pod_rev}" && "${pod_rev}" != "${update_rev}" && "${ready}" != "true" ]]; then
        log_warn "Gateway pod is failing on the previous configuration; replacing it with the new one."
        oc -n "${NAMESPACE}" delete pod openshell-0 --wait=false
    fi

    oc -n "${NAMESPACE}" rollout status statefulset/openshell --timeout=300s

    log_info "OpenShell is ready!"
}

# Create OpenShift route
create_route() {
    log_step "Creating OpenShift route..."

    # Get route hostname
    INGRESS_DOMAIN=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')
    ROUTE_HOST="openshell-${NAMESPACE}.${INGRESS_DOMAIN}"

    # Check if route already exists
    if oc get route openshell -n "${NAMESPACE}" &> /dev/null; then
        log_info "Route already exists"
        return 0
    fi

    # Create route pointing to port 8080 (Helm's default)
    cat <<EOF | oc apply -f -
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: openshell
  namespace: ${NAMESPACE}
spec:
  to:
    kind: Service
    name: openshell
  port:
    targetPort: grpc
  tls:
    termination: passthrough
EOF
    log_info "Route created: ${ROUTE_HOST}"

    # Wait a moment for route to propagate
    sleep 2
}

# Setup CLI configuration
setup_cli() {
    log_step "Setting up CLI configuration..."

    # Get route hostname
    ROUTE_HOST=$(oc get route openshell -n "${NAMESPACE}" -o jsonpath='{.spec.host}' 2>/dev/null || true)
    if [[ -z "${ROUTE_HOST}" ]]; then
        log_error "Could not get route hostname"
        exit 1
    fi

    GATEWAY_URL="https://${ROUTE_HOST}"
    log_info "Gateway URL: ${GATEWAY_URL}"

    # Remove existing gateway if present
    openshell gateway remove "${GATEWAY_NAME}" 2>/dev/null || true

    # Add gateway
    log_info "Adding gateway to CLI..."
    openshell gateway add "${GATEWAY_URL}" --local --name "${GATEWAY_NAME}"

    # Extract TLS certificates from Kubernetes secrets
    log_info "Extracting TLS certificates..."
    CLI_CONFIG_DIR="${HOME}/.config/openshell/gateways/${GATEWAY_NAME}"
    MTLS_DIR="${CLI_CONFIG_DIR}/mtls"

    mkdir -p "${MTLS_DIR}"

    local old_umask=$(umask)
    umask 077

    oc -n "${NAMESPACE}" get secret openshell-client-tls \
        -o jsonpath='{.data.ca\.crt}' | base64 -d > "${MTLS_DIR}/ca.crt"

    oc -n "${NAMESPACE}" get secret openshell-client-tls \
        -o jsonpath='{.data.tls\.crt}' | base64 -d > "${MTLS_DIR}/tls.crt"

    oc -n "${NAMESPACE}" get secret openshell-client-tls \
        -o jsonpath='{.data.tls\.key}' | base64 -d > "${MTLS_DIR}/tls.key"

    umask "${old_umask}"

    chmod 600 "${MTLS_DIR}/tls.key"

    log_info "CLI configured successfully"
}

# Display usage information
display_info() {
    log_step "Deployment complete!"
    echo ""
    log_info "Gateway: ${GATEWAY_NAME}"
    log_info "Version: ${HELM_VERSION}"
    log_info "Gateway image: $(oc -n "${NAMESPACE}" get sts openshell -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null)"
    echo ""
    log_info "Check status:"
    echo "  openshell status"
    echo ""
    log_info "Create a sandbox:"
    echo "  openshell sandbox create --name test"
    echo ""
    log_info "List sandboxes:"
    echo "  openshell sandbox list"
    echo ""

    # Kernels before Linux 5.19 (all current RHCOS) run sandboxes in legacy read-only mode.
    local kernel
    kernel=$(oc get nodes -o jsonpath='{.items[0].status.nodeInfo.kernelVersion}' 2>/dev/null || echo unknown)
    log_warn "Node kernel: ${kernel}. Before Linux 5.19, sandboxes run in legacy read-only mode:"
    log_warn "  getpeername() returns EOPNOTSUPP, which breaks Python HTTPS clients. See the guide's Known Limitations."
    echo ""
}

# Main deployment flow
main() {
    echo "=========================================="
    echo "  OpenShell v${HELM_VERSION} Deployment"
    echo "  OpenShift / Red Hat ODH"
    echo "=========================================="
    echo ""

    check_prerequisites
    check_agent_sandbox
    setup_namespace
    install_helm
    wait_for_deployment
    create_route
    setup_cli
    display_info
}

main "$@"
