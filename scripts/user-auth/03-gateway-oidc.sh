#!/usr/bin/env bash
# Step 3: turn on user authentication in the gateway and connect an admin CLI entry.
#
# Upgrades the gateway with server.oidc: issuer, audience ${API_CLIENT}, roles from
# resource_access.${API_CLIENT}.roles (admin, user), anonymous access off. Re-registers the
# keycloak-registrar interceptor (Helm resets it). Registers the CLI gateway entry
# ${CLI_GATEWAY_NAME}, which signs in as ${AUTOMATION_CLIENT} (client credentials) for admin scripts.
set -euo pipefail
. "$(dirname "$0")/env.sh"
ROOT="$(dirname "$0")/.."

ca_cm=""
if [[ -n "${KEYCLOAK_CA_FILE}" ]]; then
    # The chart hands this file to the gateway as SSL_CERT_FILE, which replaces every trust root
    # the gateway has, not only the issuer's. Include the OpenShift service CA so the gateway
    # still trusts in-cluster services (for example the SPIRE OIDC discovery provider).
    bundle=$(mktemp)
    cat "${KEYCLOAK_CA_FILE}" > "${bundle}"
    oc -n "${NAMESPACE}" get configmap openshift-service-ca.crt -o jsonpath='{.data.service-ca\.crt}' >> "${bundle}"
    oc -n "${NAMESPACE}" create configmap keycloak-ca --from-file=ca.crt="${bundle}" \
        --dry-run=client -o yaml | oc apply -f - >/dev/null
    rm -f "${bundle}"
    ca_cm=keycloak-ca
    log "gateway and dashboard proxy will trust the CA in ConfigMap keycloak-ca"
fi

OPENSHELL_OIDC_ISSUER="${ISSUER}" OPENSHELL_OIDC_AUDIENCE="${GATEWAY_AUDIENCE}" \
    OPENSHELL_OIDC_ROLES_CLAIM="${ROLES_CLAIM}" OPENSHELL_OIDC_ADMIN_ROLE=admin OPENSHELL_OIDC_USER_ROLE=user \
    OPENSHELL_OIDC_CA_CONFIGMAP="${ca_cm}" OPENSHELL_ENABLE_SPIFFE=true "${ROOT}/deploy-openshell.sh"
if oc -n "${NAMESPACE}" get deploy/keycloak-registrar >/dev/null 2>&1; then
    "${ROOT}/token-exchange/05-deploy-registrar.sh"
fi

url="https://$(oc -n "${NAMESPACE}" get route openshell -o jsonpath='{.spec.host}')"
openshell gateway remove "${CLI_GATEWAY_NAME}" >/dev/null 2>&1 || true
dir="${HOME}/.config/openshell/gateways/${CLI_GATEWAY_NAME}/mtls"
mkdir -p "${dir}"
oc -n "${NAMESPACE}" get secret openshell-client-tls -o jsonpath='{.data.ca\.crt}' | base64 -d > "${dir}/ca.crt"
OPENSHELL_OIDC_CLIENT_SECRET="$(oc -n "${NAMESPACE}" get secret openshell-automation-oidc -o jsonpath='{.data.client-secret}' | base64 -d)" \
    openshell gateway add "${url}" --name "${CLI_GATEWAY_NAME}" --oidc-issuer "${ISSUER}" \
    --oidc-client-id "${AUTOMATION_CLIENT}" --oidc-audience "${GATEWAY_AUDIENCE}"
log "gateway requires OIDC; CLI entry ${CLI_GATEWAY_NAME} signs in as ${AUTOMATION_CLIENT}"
