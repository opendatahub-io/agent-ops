#!/bin/bash
#
# Provision a Keycloak realm with demo users for the RedBank MCP server.
#
# Requires: curl, jq
#
# Environment variables:
#   KEYCLOAK_URL      Keycloak base URL (required, or auto-detected from oc route)
#   KEYCLOAK_ADMIN    Admin username (required)
#   KEYCLOAK_PASSWORD Admin password (required)

set -euo pipefail

if [[ -z "${KEYCLOAK_URL:-}" ]]; then
  KEYCLOAK_URL="https://$(oc get route keycloak -n keycloak -o jsonpath='{.spec.host}' 2>/dev/null)" || true
fi
if [[ -z "${KEYCLOAK_URL}" || "${KEYCLOAK_URL}" == "https://" ]]; then
  echo "ERROR: KEYCLOAK_URL is required. Set it or ensure 'oc get route keycloak -n keycloak' works." >&2
  exit 1
fi

KEYCLOAK_ADMIN="${KEYCLOAK_ADMIN:?KEYCLOAK_ADMIN is required}"
KEYCLOAK_PASSWORD="${KEYCLOAK_PASSWORD:?KEYCLOAK_PASSWORD is required}"

REALM="redbank"
CLIENT_ID="redbank-mcp"

function _out() {
  echo "$(date +'%F %H:%M:%S') $@"
}

function get_admin_token() {
  curl -sf "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
    -d "grant_type=password" \
    -d "client_id=admin-cli" \
    -d "username=${KEYCLOAK_ADMIN}" \
    -d "password=${KEYCLOAK_PASSWORD}" | jq -r '.access_token'
}

function kc_api() {
  local method="$1"
  local path="$2"
  shift 2
  curl -sf -X "${method}" \
    "${KEYCLOAK_URL}/admin/realms${path}" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    "$@"
}

# --- Get admin token ---------------------------------------------------------

_out "Authenticating as ${KEYCLOAK_ADMIN}"
TOKEN=$(get_admin_token)
if [[ -z "$TOKEN" || "$TOKEN" == "null" ]]; then
  echo "ERROR: Failed to get admin token" >&2
  exit 1
fi
_out "Admin token acquired"

# --- Create realm ------------------------------------------------------------

_out "Creating realm '${REALM}'"
kc_api POST "" -d "{
  \"realm\": \"${REALM}\",
  \"enabled\": true,
  \"registrationAllowed\": false
}" 2>/dev/null || _out "Realm '${REALM}' already exists"

# --- Create client -----------------------------------------------------------

_out "Creating client '${CLIENT_ID}'"
kc_api POST "/${REALM}/clients" -d "{
  \"clientId\": \"${CLIENT_ID}\",
  \"enabled\": true,
  \"publicClient\": true,
  \"directAccessGrantsEnabled\": true,
  \"standardFlowEnabled\": false,
  \"protocol\": \"openid-connect\"
}" 2>/dev/null || _out "Client '${CLIENT_ID}' already exists"

# --- Add audience mapper to client -------------------------------------------

_out "Adding audience mapper to '${CLIENT_ID}'"
CLIENT_UUID=$(kc_api GET "/${REALM}/clients?clientId=${CLIENT_ID}" | jq -r '.[0].id')

if [[ -n "$CLIENT_UUID" && "$CLIENT_UUID" != "null" ]]; then
  kc_api POST "/${REALM}/clients/${CLIENT_UUID}/protocol-mappers/models" -d "{
    \"name\": \"redbank-mcp-audience\",
    \"protocol\": \"openid-connect\",
    \"protocolMapper\": \"oidc-audience-mapper\",
    \"config\": {
      \"included.custom.audience\": \"redbank-mcp\",
      \"id.token.claim\": \"false\",
      \"access.token.claim\": \"true\"
    }
  }" 2>/dev/null || _out "Audience mapper already exists"
  _out "Audience mapper configured (aud will include 'redbank-mcp')"
else
  echo "WARNING: Could not find client UUID for '${CLIENT_ID}'" >&2
fi

# --- Create realm role -------------------------------------------------------

_out "Creating realm role 'admin'"
kc_api POST "/${REALM}/roles" -d "{
  \"name\": \"admin\",
  \"description\": \"Full access to all customer data and write operations\"
}" 2>/dev/null || _out "Role 'admin' already exists"

# --- Create users ------------------------------------------------------------

function create_user() {
  local username="$1"
  local email="$2"
  local password="$3"
  local first="$4"
  local last="$5"

  _out "Creating user '${username}' (${email})"
  kc_api POST "/${REALM}/users" -d "{
    \"username\": \"${username}\",
    \"email\": \"${email}\",
    \"emailVerified\": true,
    \"enabled\": true,
    \"firstName\": \"${first}\",
    \"lastName\": \"${last}\",
    \"credentials\": [{
      \"type\": \"password\",
      \"value\": \"${password}\",
      \"temporary\": false
    }]
  }" 2>/dev/null || _out "User '${username}' already exists"
}

create_user "john" "john@redbank.demo" "john123" "John" "Doe"
create_user "jane" "jane@redbank.demo" "jane123" "Jane" "Admin"

# --- Assign admin role to jane -----------------------------------------------

_out "Assigning 'admin' role to jane"

JANE_ID=$(kc_api GET "/${REALM}/users?username=jane&exact=true" | jq -r '.[0].id')
ADMIN_ROLE=$(kc_api GET "/${REALM}/roles/admin")

if [[ -n "$JANE_ID" && "$JANE_ID" != "null" ]]; then
  kc_api POST "/${REALM}/users/${JANE_ID}/role-mappings/realm" \
    -d "[${ADMIN_ROLE}]" 2>/dev/null || _out "Role already assigned"
  _out "Admin role assigned to jane (id=${JANE_ID})"
else
  echo "WARNING: Could not find jane's user ID" >&2
fi

# --- Verify ------------------------------------------------------------------

_out "Verifying: fetching token for john"
JOHN_TOKEN=$(curl -sf "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=${CLIENT_ID}" \
  -d "username=john" \
  -d "password=john123" | jq -r '.access_token')

if [[ -n "$JOHN_TOKEN" && "$JOHN_TOKEN" != "null" ]]; then
  _out "Token for john acquired successfully"
else
  echo "ERROR: Could not get token for john" >&2
  exit 1
fi

_out "Verifying: fetching token for jane"
JANE_TOKEN=$(curl -sf "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=${CLIENT_ID}" \
  -d "username=jane" \
  -d "password=jane123" | jq -r '.access_token')

if [[ -n "$JANE_TOKEN" && "$JANE_TOKEN" != "null" ]]; then
  _out "Token for jane acquired successfully"
else
  echo "ERROR: Could not get token for jane" >&2
  exit 1
fi

_out ""
_out "Keycloak setup complete!"
_out "  Realm:    ${REALM}"
_out "  Client:   ${CLIENT_ID}"
_out "  Users:    john (user), jane (admin)"
_out "  Token EP: ${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token"
_out "  JWKS:     ${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/certs"
