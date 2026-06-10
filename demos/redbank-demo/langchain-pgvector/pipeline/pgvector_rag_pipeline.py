"""KFP pipeline — ingest RedBank PDFs into PGVector with role-scoped collections."""

from kfp import compiler, dsl

BASE_IMAGE = "registry.access.redhat.com/ubi9/python-312:latest"

PACKAGES = [
    "langchain-postgres>=0.0.13",
    "langchain-community>=0.3.17",
    "langchain-huggingface>=0.1.2",
    "sentence-transformers>=3.3.1",
    "psycopg[binary]>=3.2.3",
    "pypdf>=5.1.0",
    "requests>=2.32.3",
]


@dsl.component(base_image=BASE_IMAGE, packages_to_install=PACKAGES)
def ingest_documents(
    base_url: str,
    filenames: str,
    collection_name: str,
    pg_host: str,
    pg_port: str,
    pg_database: str,
    pg_user: str,
    pg_password: str,
    chunk_size: int,
    chunk_overlap: int,
) -> int:
    """Download PDFs, chunk them, embed with nomic-embed-text, and store in PGVector."""
    import os
    import tempfile

    import requests
    from langchain_community.document_loaders import PyPDFLoader
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_postgres import PGEngine, PGVectorStore
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    # Build document URLs from base_url + filenames
    urls = [f"{base_url.rstrip('/')}/{f.strip()}" for f in filenames.split(",")]

    # Download PDFs and load pages
    all_docs = []
    for url in urls:
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name

        loader = PyPDFLoader(tmp_path)
        all_docs.extend(loader.load())
        os.unlink(tmp_path)

    # Chunk
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    chunks = splitter.split_documents(all_docs)

    # Tag each chunk with its collection
    for chunk in chunks:
        chunk.metadata["collection"] = collection_name

    # Embed + store
    embeddings = HuggingFaceEmbeddings(model_name="nomic-ai/nomic-embed-text-v1.5")

    # Set app.current_role=admin via connection options so RLS allows writes
    from urllib.parse import quote
    connection_string = (
        f"postgresql+psycopg://{pg_user}:{pg_password}"
        f"@{pg_host}:{pg_port}/{pg_database}"
        f"?options={quote('-c app.current_role=admin')}"
    )
    engine = PGEngine.from_connection_string(url=connection_string)

    store = PGVectorStore.create_sync(
        engine=engine,
        table_name="embeddings",
        embedding_service=embeddings,
        metadata_columns=["collection"],
    )
    store.add_documents(chunks)

    return len(chunks)


@dsl.pipeline(
    name="pgvector-rag-pipeline",
    description="Ingest RedBank PDFs into PGVector with admin/user collections",
)
def pgvector_rag_pipeline(
    base_url: str = "https://raw.githubusercontent.com/opendatahub-io/agent-ops/main/demos/redbank-demo/langchain-pgvector/docs",
    admin_filenames: str = "admin/redbank_compliance_procedures.pdf,admin/redbank_transaction_operations.pdf,admin/redbank_user_management.pdf",
    user_filenames: str = "user/redbank_account_selfservice.pdf,user/redbank_password_and_security.pdf,user/redbank_payments_and_transfers.pdf",
    pg_host: str = "postgresql.redbank-demo.svc.cluster.local",
    pg_port: str = "5432",
    pg_database: str = "db",
    pg_user: str = "app",
    pg_password: str = "app",
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
):
    # Ingest admin and user document sets in parallel
    ingest_documents(
        base_url=base_url,
        filenames=admin_filenames,
        collection_name="admin",
        pg_host=pg_host,
        pg_port=pg_port,
        pg_database=pg_database,
        pg_user=pg_user,
        pg_password=pg_password,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    ingest_documents(
        base_url=base_url,
        filenames=user_filenames,
        collection_name="user",
        pg_host=pg_host,
        pg_port=pg_port,
        pg_database=pg_database,
        pg_user=pg_user,
        pg_password=pg_password,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )


if __name__ == "__main__":
    compiler.Compiler().compile(pgvector_rag_pipeline, "pgvector_rag_pipeline.yaml")
    print("Compiled: pgvector_rag_pipeline.yaml")
