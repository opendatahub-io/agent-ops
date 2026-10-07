#!/usr/bin/env bash
# Step 1: publish Keycloak on an HTTPS Route and make that URL its issuer.
#
# The gateway only accepts an HTTPS issuer, and the issuer in a token must be the same URL
# that the browser, the CLI, the gateway and the sandboxes use. One public Route gives all
# of them one issuer. Dev-mode Keycloak keeps its realm in memory, so the restart wipes it:
# this step re-runs token-exchange steps 3 and 4 against the new issuer.
set -euo pipefail
. "$(dirname "$0")/env.sh"
TE="$(dirname "$0")/../token-exchange"

if ! oc -n "${KEYCLOAK_NAMESPACE}" get route keycloak >/dev/null 2>&1; then
    oc -n "${KEYCLOAK_NAMESPACE}" create route edge keycloak --service=keycloak \
        --hostname="${KEYCLOAK_HOST}" --insecure-policy=Redirect >/dev/null
fi
log "Keycloak Route: https://${KEYCLOAK_HOST}"

"${TE}/03-deploy-keycloak.sh"
"${TE}/04-configure-realm.sh"

for _ in $(seq 1 30); do
    iss=$(curl -s -m 10 "${ISSUER}/.well-known/openid-configuration" | jq -r '.issuer // empty' 2>/dev/null || true)
    [[ "${iss}" == "${ISSUER}" ]] && break
    sleep 5
done
[[ "${iss}" == "${ISSUER}" ]] || { echo "ERROR: issuer is '${iss}', want ${ISSUER}" >&2; exit 1; }
log "issuer is ${ISSUER}"
