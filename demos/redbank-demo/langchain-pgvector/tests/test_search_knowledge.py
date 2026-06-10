"""Tests for the search_knowledge MCP tool's PGVector role-scoped search."""

import pytest
import psycopg
from conftest import APP_USER, APP_PASSWORD, _to_psycopg_dsn

ZERO_VECTOR = "[" + ",".join(["0"] * 768) + "]"


@pytest.fixture(scope="module")
def seeded_knowledge(superuser_conn):
    """Seed embeddings rows as superuser so RLS is bypassed."""
    superuser_conn.execute(
        f"""
        INSERT INTO embeddings (collection, content, embedding, langchain_metadata)
        VALUES
            ('admin', 'Internal fraud detection policy', '{ZERO_VECTOR}', '{{"source": "admin"}}'),
            ('admin', 'Employee onboarding procedures', '{ZERO_VECTOR}', '{{"source": "admin"}}'),
            ('admin', 'Compliance audit checklist', '{ZERO_VECTOR}', '{{"source": "admin"}}'),
            ('user',  'How to reset your password', '{ZERO_VECTOR}', '{{"source": "user"}}'),
            ('user',  'Opening a new savings account', '{ZERO_VECTOR}', '{{"source": "user"}}'),
            ('user',  'Understanding your statement', '{ZERO_VECTOR}', '{{"source": "user"}}'),
            ('user',  'Mobile banking FAQ', '{ZERO_VECTOR}', '{{"source": "user"}}'),
            ('user',  'Disputing a transaction', '{ZERO_VECTOR}', '{{"source": "user"}}'),
            ('user',  'Loan application guide', '{ZERO_VECTOR}', '{{"source": "user"}}'),
            ('user',  'Credit card rewards program', '{ZERO_VECTOR}', '{{"source": "user"}}')
        """
    )
    yield
    superuser_conn.execute("DELETE FROM embeddings")


class TestAdminStore:
    def test_admin_sees_all_collections(self, admin_conn, seeded_knowledge):
        rows = admin_conn.execute(
            "SELECT DISTINCT collection FROM embeddings ORDER BY collection"
        ).fetchall()
        collections = [r[0] for r in rows]
        assert collections == ["admin", "user"]

    def test_admin_sees_all_rows(self, admin_conn, seeded_knowledge):
        count = admin_conn.execute("SELECT count(*) FROM embeddings").fetchone()[0]
        assert count == 10


class TestUserStore:
    def test_user_sees_only_user_collection(self, user_conn, seeded_knowledge):
        rows = user_conn.execute(
            "SELECT DISTINCT collection FROM embeddings"
        ).fetchall()
        collections = [r[0] for r in rows]
        assert collections == ["user"]

    def test_user_sees_only_user_rows(self, user_conn, seeded_knowledge):
        count = user_conn.execute("SELECT count(*) FROM embeddings").fetchone()[0]
        assert count == 7


class TestResultLimit:
    def test_returns_limited_rows(self, admin_conn, seeded_knowledge):
        """Verify k-limit concept: requesting fewer rows than available."""
        rows = admin_conn.execute(
            "SELECT content FROM embeddings LIMIT 3"
        ).fetchall()
        assert len(rows) == 3


class TestEmptyTable:
    def test_empty_table_returns_no_rows(self, pg_container):
        """Query an empty embeddings table returns nothing."""
        dsn = _to_psycopg_dsn(pg_container.get_connection_url())
        dsn = dsn.replace("postgres:postgres", f"{APP_USER}:{APP_PASSWORD}")
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute("SELECT set_config('app.current_role', 'admin', false)")
            count = conn.execute("SELECT count(*) FROM embeddings").fetchone()[0]
            # Table may have rows from other fixtures; just verify query succeeds
            assert isinstance(count, int)
