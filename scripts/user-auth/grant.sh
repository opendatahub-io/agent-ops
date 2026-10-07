#!/usr/bin/env bash
# Give a user access to a workspace: grant.sh <username> <workspace> [user|admin]
# Adds the user to the Keycloak group ${GROUP_PREFIX}-ws-<workspace>-<role>s (created if needed,
# carrying the gateway "user" role), then syncs workspace membership. The user signs in again
# (or waits for the session to refresh) to pick up the role.
set -euo pipefail
. "$(dirname "$0")/env.sh"
user=${1:?usage: grant.sh <username> <workspace> [user|admin]}; ws=${2:?workspace}; role=${3:-user}
[[ "${ws}" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || { echo "ERROR: invalid workspace name" >&2; exit 1; }
[[ "${role}" == user || "${role}" == admin ]] || { echo "ERROR: role must be user or admin" >&2; exit 1; }
[[ "${user}" =~ ^[A-Za-z0-9._@-]+$ ]] || { echo "ERROR: invalid username" >&2; exit 1; }

kc_exec U="${user}" G="${GROUP_PREFIX}-ws-${ws}-${role}s" API="${API_CLIENT}" <<'IN'
set -euo pipefail
KC=/opt/keycloak/bin/kcadm.sh; CFG=(--config /tmp/kcadm.config)
$KC config credentials "${CFG[@]}" --server http://localhost:8080 --realm master --user admin --password "$KC_ADMIN_PW" >/dev/null 2>&1
gid() {  # no awk in the Keycloak image
    while IFS=, read -r i n; do [ "$n" = "$G" ] && { echo "$i"; return; }; done \
        < <($KC get groups -r "$R" "${CFG[@]}" -q search="$G" -q exact=true --fields id,name --format csv --noquotes)
}
[ -n "$(gid)" ] || $KC create groups -r "$R" "${CFG[@]}" -s name="$G" >/dev/null
$KC add-roles -r "$R" "${CFG[@]}" --gid "$(gid)" --cclientid "$API" --rolename user
u=$($KC get users -r "$R" "${CFG[@]}" -q username="$U" -q exact=true --fields id --format csv --noquotes | head -1)
[ -n "$u" ] || { echo "ERROR: no user $U" >&2; exit 1; }
$KC update "users/$u/groups/$(gid)" -r "$R" "${CFG[@]}" -n >/dev/null
IN
log "${user} added to ${GROUP_PREFIX}-ws-${ws}-${role}s"
"$(dirname "$0")/05-sync-members.sh"
