# Copyright 2025 IBM, Red Hat
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

logger = logging.getLogger("redbank_mcp_db")


def _build_conninfo() -> str:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DATABASE", "db")
    user = os.getenv("POSTGRES_USER", "user")
    password = os.getenv("POSTGRES_PASSWORD", "pass")
    return f"host={host} port={port} dbname={database} user={user} password={password}"


_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Lazy-initialize and return the shared connection pool."""
    global _pool
    if _pool is None:
        conninfo = _build_conninfo()
        _pool = ConnectionPool(
            conninfo,
            min_size=2,
            max_size=10,
            kwargs={"row_factory": dict_row, "autocommit": False},
        )
        logger.info("Database connection pool created")
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        logger.info("Database connection pool closed")


@contextmanager
def get_connection() -> Generator[psycopg.Connection, None, None]:
    """Checkout a connection from the pool with automatic return."""
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


@contextmanager
def auth_connection(
    email: str, role: str
) -> Generator[psycopg.Connection, None, None]:
    """Checkout a connection and configure RLS session variables.

    Sets ``app.current_user_email`` and ``app.current_role`` via SET LOCAL
    inside a transaction so the values are scoped to this connection's
    checkout and automatically cleared on return.
    """
    with get_connection() as conn:
        with conn.transaction():
            # Drop superuser/BYPASSRLS for this transaction by switching to the
            # unprivileged 'app' role — required for PostgreSQL RLS to take effect.
            # The pool connects as the superuser 'user'; SET ROLE scopes the
            # privilege drop to this transaction only.
            conn.execute("SET LOCAL ROLE app")
            conn.execute(
                "SELECT set_config('app.current_user_email', %s, true)", (email,)
            )
            conn.execute("SELECT set_config('app.current_role', %s, true)", (role,))
            yield conn
