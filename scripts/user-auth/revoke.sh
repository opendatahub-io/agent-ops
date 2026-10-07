#!/usr/bin/env bash
# Remove a user's access to a workspace: revoke.sh <username> <workspace>
# Removes the user from both workspace groups and syncs: the membership is gone at once, so even a
# token issued before the revoke is denied in that workspace. Run without a workspace to remove the
# user from every workspace group of this gateway.
set -euo pipefail
. "$(dirname "$0")/env.sh"
user=${1:?usage: revoke.sh <username> [workspace]}; ws=${2:-}
[[ "${user}" =~ ^[A-Za-z0-9._@-]+$ ]] || { echo "ERROR: invalid username" >&2; exit 1; }
[[ -z "${ws}" || "${ws}" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ ]] || { echo "ERROR: invalid workspace name" >&2; exit 1; }
pattern="^${GROUP_PREFIX}-ws-${ws:-.+}-(users|admins)\$"

kc_exec U="${user}" P="${pattern}" <<'IN'
set -euo pipefail
KC=/opt/keycloak/bin/kcadm.sh; CFG=(--config /tmp/kcadm.config)
$KC config credentials "${CFG[@]}" --server http://localhost:8080 --realm master --user admin --password "$KC_ADMIN_PW" >/dev/null 2>&1
u=$($KC get users -r "$R" "${CFG[@]}" -q username="$U" -q exact=true --fields id --format csv --noquotes | head -1)
[ -n "$u" ] || { echo "ERROR: no user $U" >&2; exit 1; }
$KC get "users/$u/groups" -r "$R" "${CFG[@]}" --fields id,name --format csv --noquotes | while IFS=, read -r g name; do
    if [[ "$name" =~ $P ]]; then $KC delete "users/$u/groups/$g" -r "$R" "${CFG[@]}"; echo "removed from $name"; fi
done
IN
"$(dirname "$0")/05-sync-members.sh"
