#!/usr/bin/env bash
# Shared settings for the token-exchange scripts. Source it; override any value
# by exporting it before running a script.

export NAMESPACE="${OPENSHELL_NAMESPACE:-openshell}"           # OpenShell gateway namespace
export RELEASE="${OPENSHELL_RELEASE:-openshell}"               # OpenShell Helm release name
export KEYCLOAK_NAMESPACE="${KEYCLOAK_NAMESPACE:-${NAMESPACE}}"
export REALM="${KEYCLOAK_REALM:-openshell}"
export TARGET_API="${TARGET_API:-whoami-api}"                  # Keycloak audience of the API agents call
export DEMO_USER="${DEMO_USER:-alice}"

# Trust domain configured in the ZeroTrustWorkloadIdentityManager CR, as a SPIFFE URI.
if [[ -z "${TRUST_DOMAIN:-}" ]]; then
    td=$(oc get zerotrustworkloadidentitymanager cluster -o jsonpath='{.spec.trustDomain}' 2>/dev/null || true)
    if [[ -z "${td}" ]]; then
        echo "ERROR: could not read spec.trustDomain from the ZeroTrustWorkloadIdentityManager CR. Install ZTWIM first." >&2
        exit 1
    fi
    export TRUST_DOMAIN="spiffe://${td}"
fi

# In-cluster by default; scripts/user-auth/env.sh switches it to the public HTTPS Route.
export KEYCLOAK_URL="${KEYCLOAK_URL:-http://keycloak.${KEYCLOAK_NAMESPACE}.svc.cluster.local}"
export ISSUER="${KEYCLOAK_URL}/realms/${REALM}"
# In-cluster callers (token endpoint, JWKS) use the Service directly: no Route, no certificate trust.
# Tokens still carry ISSUER, because Keycloak's hostname is fixed to it.
export REALM_INTERNAL_URL="http://keycloak.${KEYCLOAK_NAMESPACE}.svc.cluster.local/realms/${REALM}"
export GATEWAY_SPIFFE_ID="${TRUST_DOMAIN}/ns/${NAMESPACE}/sa/${RELEASE}"
export SPIRE_JWKS="https://spire-spiffe-oidc-discovery-provider.openshift-ztwim.svc.cluster.local/keys"

log() { echo -e "\033[0;32m[$(basename "$0")]\033[0m $*"; }
