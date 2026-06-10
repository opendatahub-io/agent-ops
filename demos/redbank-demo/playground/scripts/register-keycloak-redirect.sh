#!/bin/bash
#
# Register the playground's public URL as a valid OAuth redirect URI
# in the Keycloak client. Run this after deploying the playground so
# the route already exists.
#
# Idempotent — safe to run multiple times.  Merges the playground URL
# into whatever redirect URIs the client already has.
#
# Requires: curl, jq, oc (logged in)
#
# Environment variables (all optional — auto-detected when possible):
#   KEYCLOAK_URL        Keycloak base URL (auto-detected from oc route)
#   KEYCLOAK_REALM      Realm name (default: redbank)
#   KEYCLOAK_CLIENT_ID  Client name (default: redbank-mcp)
#   KEYCLOAK_ADMIN      Admin username (auto-detected from k8s secret)
#   KEYCLOAK_PASSWORD   Admin password (auto-detected from k8s secret)
#   NAMESPACE           Namespace where the playground is deployed (default: redbank-demo)
#   PLAYGROUND_URL      Playground public URL (auto-detected from oc route)

set -euo pipefail

NAMESPACE="${NAMESPACE:-redbank-demo}"
REALM="${KEYCLOAK_REALM:-redbank}"
CLIENT="${KEYCLOAK_CLIENT_ID:-redbank-mcp}"

function _out() {
  echo "$(date +'%F %H:%M:%S') $*"
}

# --- Auto-detect Keycloak URL ------------------------------------------------

if [[ -z "${KEYCLOAK_URL:-}" ]]; then
  _host=$(oc get route keycloak -n keycloak -o jsonpath='{.spec.host}' 2>/dev/null) || true
  if [[ -n "${_host}" ]]; then
    KEYCLOAK_URL="https://${_host}"
  else
    echo "ERROR: Cannot detect KEYCLOAK_URL. Set it or ensure 'oc get route keycloak -n keycloak' works." >&2
    exit 1
  fi
fi
_out "Keycloak URL: ${KEYCLOAK_URL}"

# --- Auto-detect admin credentials -------------------------------------------

if [[ -z "${KEYCLOAK_ADMIN:-}" || -z "${KEYCLOAK_PASSWORD:-}" ]]; then
  KEYCLOAK_ADMIN=$(oc get secret keycloak-initial-admin -n keycloak -o jsonpath='{.data.username}' 2>/dev/null | base64 -d) || true
  KEYCLOAK_PASSWORD=$(oc get secret keycloak-initial-admin -n keycloak -o jsonpath='{.data.password}' 2>/dev/null | base64 -d) || true
fi

if [[ -z "${KEYCLOAK_ADMIN}" || -z "${KEYCLOAK_PASSWORD}" ]]; then
  echo "ERROR: Cannot detect Keycloak admin credentials. Set KEYCLOAK_ADMIN and KEYCLOAK_PASSWORD." >&2
  exit 1
fi

# --- Auto-detect playground URL ----------------------------------------------

if [[ -z "${PLAYGROUND_URL:-}" ]]; then
  _pg_host=$(oc get route redbank-playground -n "${NAMESPACE}" -o jsonpath='{.spec.host}' 2>/dev/null) || true
  if [[ -n "${_pg_host}" ]]; then
    PLAYGROUND_URL="https://${_pg_host}"
  else
    echo "ERROR: Cannot detect playground URL. Is the playground deployed in namespace '${NAMESPACE}'?" >&2
    echo "  Set PLAYGROUND_URL explicitly or deploy the playground first." >&2
    exit 1
  fi
fi
_out "Playground URL: ${PLAYGROUND_URL}"

# --- Get admin token ---------------------------------------------------------

_out "Authenticating as ${KEYCLOAK_ADMIN}"
TOKEN=$(curl -sk "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=admin-cli" \
  -d "username=${KEYCLOAK_ADMIN}" \
  -d "password=${KEYCLOAK_PASSWORD}" | jq -r '.access_token')

if [[ -z "$TOKEN" || "$TOKEN" == "null" ]]; then
  echo "ERROR: Failed to get admin token" >&2
  exit 1
fi

# --- Find client UUID --------------------------------------------------------

CLIENT_UUID=$(curl -sk \
  "${KEYCLOAK_URL}/admin/realms/${REALM}/clients?clientId=${CLIENT}" \
  -H "Authorization: Bearer ${TOKEN}" | jq -r '.[0].id')

if [[ -z "$CLIENT_UUID" || "$CLIENT_UUID" == "null" ]]; then
  echo "ERROR: Client '${CLIENT}' not found in realm '${REALM}'. Run setup-keycloak.sh first." >&2
  exit 1
fi
_out "Client UUID: ${CLIENT_UUID}"

# --- Read current client config ----------------------------------------------

CURRENT=$(curl -sk \
  "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}" \
  -H "Authorization: Bearer ${TOKEN}")

# --- Merge redirect URIs (preserve existing, add ours) -----------------------

NEW_REDIRECT="${PLAYGROUND_URL}/*"
NEW_ORIGIN="${PLAYGROUND_URL}"

# Build updated arrays: add our URI if not already present
UPDATED_REDIRECTS=$(echo "$CURRENT" | jq --arg uri "$NEW_REDIRECT" '
  .redirectUris // [] |
  if any(. == $uri) then . else . + [$uri] end
')

UPDATED_ORIGINS=$(echo "$CURRENT" | jq --arg uri "$NEW_ORIGIN" '
  .webOrigins // [] |
  if any(. == $uri) then . else . + [$uri] end
')

# Merge post_logout_redirect_uris (## separated string)
CURRENT_LOGOUT=$(echo "$CURRENT" | jq -r '.attributes["post.logout.redirect.uris"] // ""')
if [[ "$CURRENT_LOGOUT" != *"${PLAYGROUND_URL}"* ]]; then
  if [[ -n "$CURRENT_LOGOUT" ]]; then
    NEW_LOGOUT="${CURRENT_LOGOUT}##${NEW_REDIRECT}"
  else
    NEW_LOGOUT="${NEW_REDIRECT}"
  fi
else
  NEW_LOGOUT="$CURRENT_LOGOUT"
fi

# --- Update client -----------------------------------------------------------

_out "Updating client '${CLIENT}': standardFlowEnabled=true, adding redirect URI"
HTTP_CODE=$(curl -sk -o /dev/null -w "%{http_code}" -X PUT \
  "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$(echo "$CURRENT" | jq \
    --argjson redirects "$UPDATED_REDIRECTS" \
    --argjson origins "$UPDATED_ORIGINS" \
    --arg logout "$NEW_LOGOUT" '
    .standardFlowEnabled = true |
    .redirectUris = $redirects |
    .webOrigins = $origins |
    .attributes["post.logout.redirect.uris"] = $logout
  ')")

if [[ "$HTTP_CODE" == "204" ]]; then
  _out "Client updated successfully"
else
  echo "ERROR: Client update failed (HTTP ${HTTP_CODE})" >&2
  exit 1
fi

# --- Verify ------------------------------------------------------------------

_out ""
_out "Keycloak redirect registration complete!"
_out "  Client:       ${CLIENT}"
_out "  Redirect URI: ${NEW_REDIRECT}"
_out "  Web Origin:   ${NEW_ORIGIN}"
_out ""
_out "The playground login flow should now work at:"
_out "  ${PLAYGROUND_URL}/"
