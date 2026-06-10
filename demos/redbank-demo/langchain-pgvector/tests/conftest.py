"""Shared test fixtures — spins up a pgvector container via Podman and applies init.sql."""

import os
from pathlib import Path

import psycopg
import pytest
from testcontainers.postgres import PostgresContainer

INIT_SQL = Path(__file__).resolve().parents[2] / "postgres-db" / "init.sql"
PG_USER = "user"
PG_PASSWORD = "pass"
PG_DATABASE = "db"
APP_USER = "app"
APP_PASSWORD = "app"


def _to_psycopg_dsn(url: str) -> str:
    """Convert a testcontainers/SQLAlchemy URL to a plain psycopg DSN.

    testcontainers returns 'postgresql+psycopg2://...' which psycopg cannot
    parse.  Strip the '+driver' portion so it becomes 'postgresql://...'.
    """
    return url.split("://", 1)[0].split("+")[0] + "://" + url.split("://", 1)[1]


def _configure_podman():
    """Configure testcontainers to use Podman.

    Ryuk (the resource reaper) does not work with Podman, so we disable it.
    We also discover the Podman socket via `podman machine inspect` if
    DOCKER_HOST is not already set.
    """
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    if "DOCKER_HOST" not in os.environ:
        import json
        import subprocess

        try:
            out = subprocess.check_output(
                ["podman", "machine", "inspect"], text=True, stderr=subprocess.DEVNULL
            )
            machines = json.loads(out)
            for m in machines:
                sock = m.get("ConnectionInfo", {}).get("PodmanSocket", {}).get("Path")
                if sock and Path(sock).exists():
                    os.environ["DOCKER_HOST"] = f"unix://{sock}"
                    break
        except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError):
            pass


_configure_podman()


@pytest.fixture(scope="session")
def pg_container():
    """Start a pgvector container and bootstrap the schema from init.sql."""
    with PostgresContainer(
        image="pgvector/pgvector:pg16",
        username="postgres",
        password="postgres",
        dbname=PG_DATABASE,
    ) as container:
        dsn = _to_psycopg_dsn(container.get_connection_url())

        with psycopg.connect(dsn, autocommit=True) as conn:
            # Create the app user role (mirrors what the RHEL image does)
            conn.execute(
                f"CREATE ROLE \"{PG_USER}\" WITH LOGIN PASSWORD '{PG_PASSWORD}'"
            )

            # Read and substitute $POSTGRESQL_USER (same as init-db.sh sed)
            sql = INIT_SQL.read_text()
            sql = sql.replace("$POSTGRESQL_USER", PG_USER)
            conn.execute(sql)

        yield container


def _app_conn(pg_container, role: str) -> psycopg.Connection:
    """Open a connection as the non-superuser app role and set app.current_role."""
    dsn = _to_psycopg_dsn(pg_container.get_connection_url())
    dsn = dsn.replace("postgres:postgres", f"{APP_USER}:{APP_PASSWORD}")
    conn = psycopg.connect(dsn, autocommit=True)
    conn.execute("SELECT set_config('app.current_role', %s, false)", [role])
    return conn


@pytest.fixture(scope="session")
def superuser_conn(pg_container):
    """psycopg connection as the superuser (postgres)."""
    dsn = _to_psycopg_dsn(pg_container.get_connection_url())
    conn = psycopg.connect(dsn, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def admin_conn(pg_container):
    """psycopg connection as table owner with app.current_role='admin'."""
    conn = _app_conn(pg_container, "admin")
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def user_conn(pg_container):
    """psycopg connection as table owner with app.current_role='user'."""
    conn = _app_conn(pg_container, "user")
    yield conn
    conn.close()
