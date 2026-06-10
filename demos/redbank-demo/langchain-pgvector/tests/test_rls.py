"""RLS tests — verify session-variable access control on the embeddings table."""

import pytest
import psycopg
from conftest import APP_USER, APP_PASSWORD, _to_psycopg_dsn


# Fake 768-dim zero vector literal for seeding
ZERO_VECTOR = "[" + ",".join(["0"] * 768) + "]"


@pytest.fixture(scope="module")
def seeded_data(superuser_conn):
    """Insert test rows as superuser so RLS is bypassed during seeding."""
    superuser_conn.execute(
        f"""
        INSERT INTO embeddings (collection, content, embedding, langchain_metadata)
        VALUES
            ('admin', 'admin doc 1', '{ZERO_VECTOR}', '{{"source": "admin"}}'),
            ('admin', 'admin doc 2', '{ZERO_VECTOR}', '{{"source": "admin"}}'),
            ('user',  'user doc 1',  '{ZERO_VECTOR}', '{{"source": "user"}}'),
            ('user',  'user doc 2',  '{ZERO_VECTOR}', '{{"source": "user"}}')
        """
    )
    yield
    superuser_conn.execute("DELETE FROM embeddings")


class TestAdminAccess:
    def test_sees_all_collections(self, admin_conn, seeded_data):
        rows = admin_conn.execute(
            "SELECT DISTINCT collection FROM embeddings ORDER BY collection"
        ).fetchall()
        collections = [r[0] for r in rows]
        assert collections == ["admin", "user"]

    def test_sees_all_rows(self, admin_conn, seeded_data):
        count = admin_conn.execute(
            "SELECT count(*) FROM embeddings"
        ).fetchone()[0]
        assert count == 4

    def test_can_insert_admin_collection(self, admin_conn, seeded_data):
        admin_conn.execute(
            f"INSERT INTO embeddings (collection, content, embedding) "
            f"VALUES ('admin', 'admin test', '{ZERO_VECTOR}')"
        )
        admin_conn.execute(
            "DELETE FROM embeddings WHERE content = 'admin test'"
        )

    def test_can_insert_user_collection(self, admin_conn, seeded_data):
        admin_conn.execute(
            f"INSERT INTO embeddings (collection, content, embedding) "
            f"VALUES ('user', 'user test by admin', '{ZERO_VECTOR}')"
        )
        admin_conn.execute(
            "DELETE FROM embeddings WHERE content = 'user test by admin'"
        )


class TestUserAccess:
    def test_sees_only_user_collection(self, user_conn, seeded_data):
        rows = user_conn.execute(
            "SELECT DISTINCT collection FROM embeddings"
        ).fetchall()
        collections = [r[0] for r in rows]
        assert collections == ["user"]

    def test_sees_only_user_rows(self, user_conn, seeded_data):
        count = user_conn.execute(
            "SELECT count(*) FROM embeddings"
        ).fetchone()[0]
        assert count == 2

    def test_cannot_see_admin_rows(self, user_conn, seeded_data):
        rows = user_conn.execute(
            "SELECT * FROM embeddings WHERE collection = 'admin'"
        ).fetchall()
        assert len(rows) == 0

    def test_cannot_insert(self, pg_container, seeded_data):
        dsn = _to_psycopg_dsn(pg_container.get_connection_url())
        dsn = dsn.replace("postgres:postgres", f"{APP_USER}:{APP_PASSWORD}")
        with psycopg.connect(dsn, autocommit=False) as conn:
            conn.execute("SELECT set_config('app.current_role', 'user', false)")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    f"INSERT INTO embeddings (collection, content, embedding) "
                    f"VALUES ('user', 'user sneaky insert', '{ZERO_VECTOR}')"
                )
