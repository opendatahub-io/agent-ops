#!/usr/bin/env bash
# Shared settings for the user-authentication (OIDC) scripts. Builds on the token-exchange
# settings and switches Keycloak to its public HTTPS Route, so the browser, the CLI, the
# gateway and the sandboxes all see one issuer.
. "$(dirname "${BASH_SOURCE[0]}")/../token-exchange/env.sh"

APPS_DOMAIN=$(oc get ingresses.config.openshift.io cluster -o jsonpath='{.spec.domain}')
export KEYCLOAK_HOST="${KEYCLOAK_HOST:-keycloak-${KEYCLOAK_NAMESPACE}.${APPS_DOMAIN}}"
export KEYCLOAK_URL="https://${KEYCLOAK_HOST}"
export ISSUER="${KEYCLOAK_URL}/realms/${REALM}"

# Private CA. Leave empty when the cluster's Route certificates are publicly trusted (preflight
# --user-auth tells you). Otherwise point it at the PEM of the CA that signs the Keycloak Route:
# the gateway and oauth2-proxy get it as ConfigMap keycloak-ca, and local curl calls trust it too.
export KEYCLOAK_CA_FILE="${KEYCLOAK_CA_FILE:-}"
if [[ -n "${KEYCLOAK_CA_FILE}" ]]; then
    [[ -f "${KEYCLOAK_CA_FILE}" ]] || { echo "ERROR: KEYCLOAK_CA_FILE ${KEYCLOAK_CA_FILE} not found" >&2; exit 1; }
    for sys in /etc/ssl/cert.pem /etc/pki/tls/certs/ca-bundle.crt /etc/ssl/certs/ca-certificates.crt; do
        [[ -f "${sys}" ]] && break
    done
    export CURL_CA_BUNDLE="$(mktemp)"   # system roots plus the private CA, so other public endpoints still verify
    cat "${sys}" "${KEYCLOAK_CA_FILE}" > "${CURL_CA_BUNDLE}"
    export SSL_CERT_FILE="${CURL_CA_BUNDLE}"   # the openshell CLI reads it for its own Keycloak calls
fi

# Everything below is per gateway, so several gateways can share one realm without a token for
# one being accepted by another (separate audience, separate role namespace, separate groups).
export API_CLIENT="${API_CLIENT:-openshell-api-${NAMESPACE}}"      # audience and role namespace
export GATEWAY_AUDIENCE="${API_CLIENT}"
export ROLES_CLAIM="resource_access.${API_CLIENT}.roles"
export CLI_CLIENT="openshell-cli-${NAMESPACE}"
export DASHBOARD_CLIENT="openshell-dashboard-${NAMESPACE}"
export AUTOMATION_CLIENT="openshell-automation-${NAMESPACE}"
export SYNC_CLIENT="openshell-member-sync-${NAMESPACE}"
# Groups are the only thing an administrator manages:
#   ${GROUP_PREFIX}-admins                  platform admins of this gateway
#   ${GROUP_PREFIX}-ws-<workspace>-users    may use workspace <workspace>
#   ${GROUP_PREFIX}-ws-<workspace>-admins   administers workspace <workspace>
export GROUP_PREFIX="${GROUP_PREFIX:-openshell-${NAMESPACE}}"

export DASHBOARD_HOST="${DASHBOARD_HOST:-openshell-dashboard-${NAMESPACE}.${APPS_DOMAIN}}"
export CLI_GATEWAY_NAME="${CLI_GATEWAY_NAME:-${NAMESPACE}}"                # CLI entry for the automation client
export BFF_LOCAL_PORT="${BFF_LOCAL_PORT:-18081}"

# Run a kcadm script inside the Keycloak pod as the bootstrap admin (evaluation installs).
kc_exec() {
    oc -n "${KEYCLOAK_NAMESPACE}" exec -i deploy/keycloak -- env \
        KC_ADMIN_PW="$(oc -n "${KEYCLOAK_NAMESPACE}" get secret keycloak-admin -o jsonpath='{.data.password}' | base64 -d)" \
        R="${REALM}" "$@" bash -s
}
