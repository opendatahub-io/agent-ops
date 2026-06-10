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

"""RedBank PostgreSQL MCP Server — Kagenti edition.

Adapted from redbank-demo/mcp-server to add:
- AuthBridge-compatible JWT auth (trusts sidecar validation, with optional standalone JWKS verify)
- RLS-aware database connections (app.current_role, app.current_user_email)
- Admin/user role enforcement
- Write tools: update_account, create_transaction
"""

from __future__ import annotations

import inspect
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
from typing import Any, Optional, Union
from urllib.parse import quote

import jwt
from jwt import PyJWKClient
import psycopg
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

from database_manager import auth_connection, close_pool
from logger import setup_logger

logger = setup_logger()

mcp = FastMCP("redbank_postgresql")
logger.info("MCP Server initialized: RedBank PostgreSQL (Kagenti)")

# ---------------------------------------------------------------------------
# Auth config
# ---------------------------------------------------------------------------
#
# Two modes of operation:
#
#   JWT_VERIFY=false (default) — "AuthBridge trusted" mode
#     The Envoy sidecar (AuthBridge) has already validated the JWT signature,
#     expiration, issuer, and audience via JWKS.  We decode without verification
#     to extract identity claims.  This is the standard Kagenti deployment model.
#
#   JWT_VERIFY=true — "standalone" mode
#     No AuthBridge sidecar in front.  The MCP server fetches signing keys from
#     JWKS_URL and verifies the JWT itself.  Use for dev clusters without Kagenti
#     or as defense-in-depth.
# ---------------------------------------------------------------------------

JWKS_URL = os.getenv("JWKS_URL", "")
JWT_VERIFY = os.getenv("JWT_VERIFY", "false").lower() == "true"
JWT_ALGORITHMS = os.getenv("JWT_ALGORITHMS", "RS256").split(",")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "")
ADMIN_ROLE_CLAIM = os.getenv("ADMIN_ROLE_CLAIM", "admin")

_jwks_client: PyJWKClient | None = None

if JWT_VERIFY and JWKS_URL:
    _jwks_client = PyJWKClient(JWKS_URL, cache_keys=True)
    logger.info("JWT verification enabled — JWKS from %s", JWKS_URL)
elif JWT_VERIFY and not JWKS_URL:
    logger.warning("JWT_VERIFY=true but no JWKS_URL set — signature verification will fail")
else:
    logger.info("JWT verification disabled — trusting AuthBridge sidecar")


@dataclass
class AuthContext:
    email: str
    role: str  # "admin" | "user"


def _extract_auth() -> AuthContext:
    """Extract user identity from the AuthBridge-forwarded JWT.

    AuthBridge (Envoy + go-processor sidecar) performs RFC 8693 token exchange
    and forwards an audience-scoped Bearer token on the Authorization header.
    This function decodes the claims to determine identity and role.
    """
    headers = get_http_headers()
    auth_header = headers.get("authorization", "")

    if not auth_header.startswith("Bearer "):
        default_role = os.getenv("DEFAULT_ROLE", "admin")
        default_email = os.getenv("DEFAULT_EMAIL", "jane@redbank.demo")
        logger.warning("No Bearer token — using defaults (role=%s)", default_role)
        return AuthContext(email=default_email, role=default_role)

    token = auth_header[7:]

    if JWT_VERIFY and _jwks_client:
        signing_key = _jwks_client.get_signing_key_from_jwt(token)
        decode_kwargs: dict[str, Any] = {
            "key": signing_key.key,
            "algorithms": JWT_ALGORITHMS,
        }
        if JWT_AUDIENCE:
            decode_kwargs["audience"] = JWT_AUDIENCE
    else:
        decode_kwargs = {
            "options": {
                "verify_signature": False,
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iss": False,
                "verify_aud": False,
            },
            "algorithms": JWT_ALGORITHMS,
        }

    claims = jwt.decode(token, **decode_kwargs)

    # Keycloak/AuthBridge claim extraction — email with fallback chain
    email = (
        claims.get("email")
        or claims.get("preferred_username")
        or claims.get("sub", "")
    )

    # Role from Keycloak realm_access, resource_access, or scope
    realm_roles = claims.get("realm_access", {}).get("roles", [])
    resource_roles = claims.get("resource_access", {}).get("account", {}).get("roles", [])
    scopes = claims.get("scope", "").split()
    all_roles = set(realm_roles) | set(resource_roles) | set(scopes)
    role = "admin" if ADMIN_ROLE_CLAIM in all_roles else "user"

    logger.info("Auth: email=%s role=%s (verify=%s)", email, role, JWT_VERIFY)
    return AuthContext(email=email, role=role)


# ---------------------------------------------------------------------------
# PGVector knowledge base (dual-store: admin + user)
# ---------------------------------------------------------------------------
#
# Pre-create two PGVectorStore instances that connect as the non-superuser
# ``app`` role, each with a different ``app.current_role`` session variable
# so that PostgreSQL RLS automatically scopes collection visibility.
# ---------------------------------------------------------------------------

_admin_store = None
_user_store = None
_kb_error: str | None = None

try:
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_postgres import PGEngine, PGVectorStore

    _EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
    _embeddings = HuggingFaceEmbeddings(
        model_name=_EMBEDDING_MODEL,
        model_kwargs={"trust_remote_code": True},
    )

    _PGVECTOR_HOST = os.getenv("POSTGRES_HOST", "localhost")
    _PGVECTOR_PORT = os.getenv("POSTGRES_PORT", "5432")
    _PGVECTOR_DB = os.getenv("POSTGRES_DATABASE", "db")
    _PGVECTOR_USER = os.getenv("PGVECTOR_USER", "app")
    _PGVECTOR_PASSWORD = os.getenv("PGVECTOR_PASSWORD", "app")

    def _pgvector_conn_string(role: str) -> str:
        opts = quote(f"-c app.current_role={role}")
        return (
            f"postgresql+psycopg://{_PGVECTOR_USER}:{_PGVECTOR_PASSWORD}"
            f"@{_PGVECTOR_HOST}:{_PGVECTOR_PORT}/{_PGVECTOR_DB}"
            f"?options={opts}"
        )

    _admin_engine = PGEngine.from_connection_string(url=_pgvector_conn_string("admin"))
    _user_engine = PGEngine.from_connection_string(url=_pgvector_conn_string("user"))

    _admin_store = PGVectorStore.create_sync(
        engine=_admin_engine,
        table_name="embeddings",
        embedding_service=_embeddings,
        metadata_columns=["collection"],
    )
    _user_store = PGVectorStore.create_sync(
        engine=_user_engine,
        table_name="embeddings",
        embedding_service=_embeddings,
        metadata_columns=["collection"],
    )
    logger.info("PGVector knowledge base initialized (model=%s)", _EMBEDDING_MODEL)

except Exception as e:
    _kb_error = str(e)
    logger.warning("PGVector knowledge base unavailable: %s", e)


# ---------------------------------------------------------------------------
# Validation helpers (carried over from original)
# ---------------------------------------------------------------------------


def validate_date(date_str: str, param_name: str) -> str:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        raise ValueError(
            f"{param_name} must be in YYYY-MM-DD format (e.g., 2025-01-15), got: {date_str}"
        )


def validate_int(value: Any, param_name: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"{param_name} must be a valid integer, got: {value}")
    raise ValueError(
        f"{param_name} must be a valid integer type, got: {type(value).__name__}"
    )


# ---------------------------------------------------------------------------
# Decorator for auth + error handling
# ---------------------------------------------------------------------------


_INJECTED_PARAMS = frozenset(("auth", "conn"))


def authenticated(func):
    """Wrap a tool handler with JWT extraction, RLS setup, and error handling.

    The wrapped function receives an ``auth`` keyword argument (AuthContext)
    and a ``conn`` keyword argument (psycopg Connection with RLS configured).
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            auth = _extract_auth()
            with auth_connection(auth.email, auth.role) as conn:
                return func(*args, auth=auth, conn=conn, **kwargs)
        except jwt.exceptions.PyJWTError as e:
            logger.error("Auth error in %s: %s", func.__name__, e)
            raise RuntimeError(f"Authentication error: {e}")
        except psycopg.Error as e:
            logger.error("Database error in %s: %s", func.__name__, e)
            raise RuntimeError(f"Database error: {e}")
        except ValueError as e:
            logger.error("Invalid input in %s: %s", func.__name__, e)
            raise RuntimeError(f"Invalid input: {e}")

    sig = inspect.signature(func)
    wrapper.__signature__ = sig.replace(
        parameters=[p for p in sig.parameters.values() if p.name not in _INJECTED_PARAMS]
    )
    return wrapper


def admin_only(func):
    """Reject non-admin callers at the application level (defense in depth)."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        auth: AuthContext = kwargs.get("auth")  # type: ignore[assignment]
        if auth is None or auth.role != "admin":
            raise RuntimeError("This operation requires admin privileges")
        return func(*args, **kwargs)

    return wrapper


# ===================================================================
# READ tools
# ===================================================================


@mcp.tool()
@authenticated
def get_customer(
    email: Optional[str] = None,
    phone: Optional[str] = None,
    *,
    auth: AuthContext,
    conn: psycopg.Connection,
) -> dict[str, Any]:
    """Get customer by email or phone number.

    Args:
        email: Customer email address
        phone: Customer phone number

    Returns:
        Customer details or empty dict if not found
    """
    if not email and not phone:
        raise ValueError("Either email or phone must be provided")

    field = "email" if email else "phone"
    value = email if email else phone
    logger.info("get_customer: %s=%s (role=%s)", field, value, auth.role)

    cur = conn.execute(
        f"""
        SELECT customer_id, name, email, phone, address, account_type,
               date_of_birth, created_date
        FROM customers
        WHERE {field} = %s
        """,
        (value,),
    )
    result = cur.fetchone()
    return dict(result) if result else {}


@mcp.tool()
@authenticated
def get_customer_transactions(
    customer_id: Union[int, str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    *,
    auth: AuthContext,
    conn: psycopg.Connection,
) -> list[dict[str, Any]]:
    """Get customer transactions with optional date filtering.

    Args:
        customer_id: Customer ID
        start_date: Optional start date (YYYY-MM-DD)
        end_date: Optional end date (YYYY-MM-DD)

    Returns:
        List of transactions
    """
    customer_id = validate_int(customer_id, "customer_id")
    logger.info("get_customer_transactions: customer=%s (role=%s)", customer_id, auth.role)

    query = """
        SELECT t.transaction_id, t.statement_id, s.customer_id,
               c.name as customer_name, t.transaction_date, t.amount,
               t.description, t.transaction_type, t.merchant
        FROM transactions t
        JOIN statements s ON t.statement_id = s.statement_id
        JOIN customers c ON s.customer_id = c.customer_id
        WHERE s.customer_id = %s
    """
    params: list[Any] = [customer_id]

    if start_date:
        start_date = validate_date(start_date, "start_date")
        query += " AND DATE(t.transaction_date) >= %s"
        params.append(start_date)

    if end_date:
        end_date = validate_date(end_date, "end_date")
        query += " AND DATE(t.transaction_date) <= %s"
        params.append(end_date)

    query += " ORDER BY t.transaction_date DESC"

    cur = conn.execute(query, tuple(params))
    return [dict(row) for row in cur.fetchall()]


@mcp.tool()
@authenticated
def get_account_summary(
    customer_id: Union[int, str],
    *,
    auth: AuthContext,
    conn: psycopg.Connection,
) -> dict[str, Any]:
    """Get account summary with customer info and latest balance.

    Args:
        customer_id: Customer ID

    Returns:
        Account summary or empty dict if not found
    """
    customer_id = validate_int(customer_id, "customer_id")
    logger.info("get_account_summary: customer=%s (role=%s)", customer_id, auth.role)

    cur = conn.execute(
        """
        SELECT
            c.customer_id, c.name, c.email, c.phone, c.address,
            c.account_type, c.date_of_birth,
            COUNT(DISTINCT s.statement_id) as total_statements,
            MAX(s.statement_id) as latest_statement_id,
            MAX(s.statement_period_end) as latest_statement_date,
            (SELECT balance FROM statements
             WHERE customer_id = c.customer_id
             ORDER BY statement_period_end DESC LIMIT 1) as latest_balance
        FROM customers c
        LEFT JOIN statements s ON c.customer_id = s.customer_id
        WHERE c.customer_id = %s
        GROUP BY c.customer_id, c.name, c.email, c.phone, c.address,
                 c.account_type, c.date_of_birth
        """,
        (customer_id,),
    )
    result = cur.fetchone()
    return dict(result) if result else {}


@mcp.tool()
@authenticated
def search_knowledge(
    query: str,
    k: int = 5,
    *,
    auth: AuthContext,
    conn: psycopg.Connection,
) -> list[dict[str, Any]]:
    """Search the RedBank knowledge base for documents matching a query.

    Uses semantic similarity search across role-scoped document collections.
    Admins see documents from all collections; users see only user-facing documents.

    Args:
        query: Natural language search query
        k: Number of results to return (default 5)

    Returns:
        List of matching documents with content, collection, and metadata
    """
    if _admin_store is None or _user_store is None:
        raise RuntimeError(
            f"Knowledge base is not available: {_kb_error or 'initialization failed'}"
        )

    store = _admin_store if auth.role == "admin" else _user_store
    logger.info("search_knowledge: query=%r k=%d role=%s", query[:80], k, auth.role)

    docs = store.similarity_search(query, k=k)
    return [
        {
            "content": doc.page_content,
            "collection": doc.metadata.get("collection", ""),
            "metadata": {
                mk: mv for mk, mv in doc.metadata.items() if mk != "collection"
            },
        }
        for doc in docs
    ]


# ===================================================================
# WRITE tools (admin only)
# ===================================================================


@mcp.tool()
@authenticated
@admin_only
def update_account(
    customer_id: Union[int, str],
    phone: Optional[str] = None,
    address: Optional[str] = None,
    account_type: Optional[str] = None,
    *,
    auth: AuthContext,
    conn: psycopg.Connection,
) -> dict[str, Any]:
    """Update customer account details (admin only).

    Args:
        customer_id: Customer ID to update
        phone: New phone number (optional)
        address: New address (optional)
        account_type: New account type (optional)

    Returns:
        Updated customer record or empty dict if not found
    """
    customer_id = validate_int(customer_id, "customer_id")
    logger.info("update_account: customer=%s (admin=%s)", customer_id, auth.email)

    updates: list[str] = []
    params: list[Any] = []
    if phone is not None:
        updates.append("phone = %s")
        params.append(phone)
    if address is not None:
        updates.append("address = %s")
        params.append(address)
    if account_type is not None:
        updates.append("account_type = %s")
        params.append(account_type)

    if not updates:
        raise ValueError("At least one field (phone, address, account_type) must be provided")

    params.append(customer_id)
    cur = conn.execute(
        f"""
        UPDATE customers
        SET {', '.join(updates)}
        WHERE customer_id = %s
        RETURNING customer_id, name, email, phone, address, account_type,
                  date_of_birth, created_date
        """,
        tuple(params),
    )
    result = cur.fetchone()
    return dict(result) if result else {}


@mcp.tool()
@authenticated
@admin_only
def create_transaction(
    customer_id: Union[int, str],
    amount: float,
    description: str,
    transaction_type: str,
    merchant: Optional[str] = None,
    transaction_date: Optional[str] = None,
    *,
    auth: AuthContext,
    conn: psycopg.Connection,
) -> dict[str, Any]:
    """Create a new transaction for a customer (admin only).

    Appends the transaction to the customer's most recent statement.

    Args:
        customer_id: Customer ID
        amount: Transaction amount (positive for credit, negative for debit)
        description: Transaction description
        transaction_type: CREDIT or DEBIT
        merchant: Merchant name (optional)
        transaction_date: Transaction date/time as YYYY-MM-DD (optional, defaults to now)

    Returns:
        The created transaction record
    """
    customer_id = validate_int(customer_id, "customer_id")
    logger.info("create_transaction: customer=%s amount=%s (admin=%s)", customer_id, amount, auth.email)

    if transaction_type not in ("CREDIT", "DEBIT"):
        raise ValueError("transaction_type must be CREDIT or DEBIT")

    # Find the customer's most recent statement
    cur = conn.execute(
        """
        SELECT statement_id FROM statements
        WHERE customer_id = %s
        ORDER BY statement_period_end DESC
        LIMIT 1
        """,
        (customer_id,),
    )
    stmt = cur.fetchone()
    if not stmt:
        raise RuntimeError(f"No statements found for customer {customer_id}")

    ts = datetime.now() if transaction_date is None else datetime.strptime(
        validate_date(transaction_date, "transaction_date"), "%Y-%m-%d"
    )

    cur = conn.execute(
        """
        INSERT INTO transactions (statement_id, transaction_date, amount,
                                  description, transaction_type, merchant)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING transaction_id, statement_id, transaction_date, amount,
                  description, transaction_type, merchant
        """,
        (stmt["statement_id"], ts, amount, description, transaction_type, merchant),
    )
    result = cur.fetchone()
    return dict(result) if result else {}


# ===================================================================
# Server entry point
# ===================================================================

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    logger.info("Starting RedBank PostgreSQL MCP server on http://%s:%s/mcp", host, port)

    try:
        mcp.run(transport="http", host=host, port=port, path="/mcp")
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
        close_pool()
    except Exception as e:
        logger.error("Server error: %s", e)
        close_pool()
        sys.exit(1)
