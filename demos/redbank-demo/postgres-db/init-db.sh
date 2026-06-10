#!/bin/bash

# This script runs at first startup via /docker-entrypoint-initdb.d/.
# Substitutes $POSTGRESQL_USER in init.sql and applies the schema + seed data.
# Supports both RHEL (POSTGRESQL_USER) and community (POSTGRES_USER) env vars.

DB_USER="${POSTGRESQL_USER:-${POSTGRES_USER:-user}}"
DB_NAME="${POSTGRESQL_DATABASE:-${POSTGRES_DB:-db}}"

echo "Running initialization SQL..."
sed "s/\$POSTGRESQL_USER/$DB_USER/g" /opt/init/init.sql | \
    psql -U "$DB_USER" -d "$DB_NAME"
echo "Initialization complete!"
