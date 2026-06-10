"""Schema tests — verify pgvector extension, embeddings table, and RLS setup."""


class TestPgvectorExtension:
    def test_vector_extension_exists(self, superuser_conn):
        row = superuser_conn.execute(
            "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        assert row is not None, "pgvector extension not installed"


class TestEmbeddingsTable:
    def test_table_exists(self, superuser_conn):
        row = superuser_conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'embeddings'"
        ).fetchone()
        assert row is not None, "embeddings table does not exist"

    def test_columns(self, superuser_conn):
        rows = superuser_conn.execute(
            "SELECT column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_name = 'embeddings' "
            "ORDER BY ordinal_position"
        ).fetchall()
        columns = {name: dtype for name, dtype in rows}
        assert "langchain_id" in columns
        assert "collection" in columns
        assert "content" in columns
        assert "embedding" in columns
        assert "langchain_metadata" in columns

    def test_primary_key(self, superuser_conn):
        row = superuser_conn.execute(
            "SELECT a.attname "
            "FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid "
            "  AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'embeddings'::regclass "
            "  AND i.indisprimary"
        ).fetchone()
        assert row is not None
        assert row[0] == "langchain_id"

    def test_collection_index_exists(self, superuser_conn):
        row = superuser_conn.execute(
            "SELECT 1 FROM pg_indexes "
            "WHERE tablename = 'embeddings' "
            "  AND indexdef LIKE '%collection%'"
        ).fetchone()
        assert row is not None, "index on collection column not found"


class TestRLS:
    def test_rls_enabled_on_embeddings(self, superuser_conn):
        row = superuser_conn.execute(
            "SELECT relrowsecurity FROM pg_class "
            "WHERE relname = 'embeddings'"
        ).fetchone()
        assert row is not None
        assert row[0] is True, "RLS is not enabled on embeddings"

    def test_rls_forced_on_embeddings(self, superuser_conn):
        row = superuser_conn.execute(
            "SELECT relforcerowsecurity FROM pg_class "
            "WHERE relname = 'embeddings'"
        ).fetchone()
        assert row is not None
        assert row[0] is True, "FORCE RLS is not enabled on embeddings"
