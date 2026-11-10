#!/bin/bash
set -euo pipefail
umask 077
if ! id -un >/dev/null 2>&1; then
  printf 'redcompass:x:%s:0:Red Compass database:/var/lib/pgsql:/sbin/nologin\n' "$(id -u)" >> /etc/passwd
fi
mkdir -p "$PGDATA"
# OpenShift fsGroup processing can add group-write bits on every remount.
chmod 700 "$PGDATA"
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  : "${DATABASE_PASSWORD:?DATABASE_PASSWORD must be set}"
  password_file=$(mktemp)
  trap 'rm -f "$password_file"' EXIT
  printf '%s' "$DATABASE_PASSWORD" > "$password_file"
  initdb -D "$PGDATA" --username=redcompass --pwfile="$password_file" --encoding=UTF8 --locale=C --auth-host=scram-sha-256 --auth-local=trust
  rm -f "$password_file"
  printf '\nhost all all 0.0.0.0/0 scram-sha-256\n' >> "$PGDATA/pg_hba.conf"
fi
exec postgres -D "$PGDATA" -c listen_addresses='*' -c unix_socket_directories=/tmp
