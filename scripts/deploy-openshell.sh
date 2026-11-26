#!/usr/bin/env bash
# OpenShell v0.0.116 deployment for OpenShift
# Based on opendatahub-io/agent-ops pinned version testing

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Configuration with defaults
NAMESPACE="${OPENSHELL_NAMESPACE:-openshell}"
GATEWAY_NAME="${OPENSHELL_GATEWAY_NAME:-openshift}"
HELM_VERSION="${OPENSHELL_HELM_VERSION:-0.0.116}"
ODH_IMAGE_TAG="${ODH_IMAGE_TAG:-v0.0.116-rhaiv.0}"
GATEWAY_IMAGE_REPOSITORY="${OPENSHELL_GATEWAY_IMAGE_REPOSITORY:-quay.io/opendatahub/odh-openshell-gateway}"
GATEWAY_IMAGE_TAG="${OPENSHELL_GATEWAY_IMAGE_TAG:-${ODH_IMAGE_TAG}}"
SUPERVISOR_IMAGE_REPOSITORY="${OPENSHELL_SUPERVISOR_IMAGE_REPOSITORY:-quay.io/opendatahub/odh-openshell-supervisor}"
SUPERVISOR_IMAGE_TAG="${OPENSHELL_SUPERVISOR_IMAGE_TAG:-${ODH_IMAGE_TAG}}"
SCC_NAME="${OPENSHELL_SCC:-openshell-sandbox-minimum-required}"

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

if [[ ! "$HELM_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "ERROR: Invalid Helm version. Must be in semver format (e.g., 0.0.116)." >&2
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

    # Add SCC permissions for OpenShift
    log_info "Adding SCC permissions for OpenShift..."

    # Check if specified SCC exists
    if oc get scc "${SCC_NAME}" &> /dev/null; then
        log_info "Using SCC: ${SCC_NAME}"
        oc adm policy add-scc-to-user "${SCC_NAME}" -z openshell-sandbox -n "${NAMESPACE}" 2>/dev/null || true
    else
        log_warn "SCC '${SCC_NAME}' not found, using privileged SCC"
        log_warn "Set OPENSHELL_SCC environment variable to use a different SCC"
        oc adm policy add-scc-to-user privileged -z openshell-sandbox -n "${NAMESPACE}" 2>/dev/null || true
    fi

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

    # Check if already installed
    if helm list -n "${NAMESPACE}" | grep -q "^openshell"; then
        log_info "OpenShell already installed, upgrading..."
        helm upgrade openshell oci://ghcr.io/nvidia/openshell/helm-chart \
            --version "${HELM_VERSION}" \
            --namespace "${NAMESPACE}" \
            --set image.repository="${GATEWAY_IMAGE_REPOSITORY}" \
            --set image.tag="${GATEWAY_IMAGE_TAG}" \
            --set supervisor.image.repository="${SUPERVISOR_IMAGE_REPOSITORY}" \
            --set supervisor.image.tag="${SUPERVISOR_IMAGE_TAG}" \
            --set podSecurityContext.fsGroup=null \
            --set securityContext.runAsUser=null \
            --set server.auth.allowUnauthenticatedUsers=true \
            --set server.telemetryEnabled=false \
            --set "pkiInitJob.serverDnsNames[0]=${ROUTE_HOSTNAME}"
    else
        log_info "Installing OpenShell ${HELM_VERSION}..."
        helm install openshell oci://ghcr.io/nvidia/openshell/helm-chart \
            --version "${HELM_VERSION}" \
            --namespace "${NAMESPACE}" \
            --set image.repository="${GATEWAY_IMAGE_REPOSITORY}" \
            --set image.tag="${GATEWAY_IMAGE_TAG}" \
            --set supervisor.image.repository="${SUPERVISOR_IMAGE_REPOSITORY}" \
            --set supervisor.image.tag="${SUPERVISOR_IMAGE_TAG}" \
            --set podSecurityContext.fsGroup=null \
            --set securityContext.runAsUser=null \
            --set server.auth.allowUnauthenticatedUsers=true \
            --set server.telemetryEnabled=false \
            --set "pkiInitJob.serverDnsNames[0]=${ROUTE_HOSTNAME}"
    fi

    # Required for OpenShell < 0.0.103 where the Helm chart does not wire
    # server.telemetryEnabled into the pod environment.
    # See https://github.com/NVIDIA/OpenShell/pull/2648
    log_info "Disabling anonymous telemetry..."
    oc set env statefulset/openshell OPENSHELL_TELEMETRY_ENABLED=false -n "${NAMESPACE}"

    log_info "OpenShell installed successfully"
}

# Wait for deployment
wait_for_deployment() {
    log_step "Waiting for OpenShell to be ready..."

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
    log_info "Gateway image: ${GATEWAY_IMAGE_REPOSITORY}:${GATEWAY_IMAGE_TAG}"
    log_info "Supervisor image: ${SUPERVISOR_IMAGE_REPOSITORY}:${SUPERVISOR_IMAGE_TAG}"
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

    # Show current SCC in use
    local current_scc="unknown"
    if oc get scc openshell-sandbox-minimum-required &> /dev/null; then
        current_scc="openshell-sandbox-minimum-required (custom)"
    else
        current_scc="privileged (default)"
    fi
    log_warn "Current SCC: ${current_scc}"
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
