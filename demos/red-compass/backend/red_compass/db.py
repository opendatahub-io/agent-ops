from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings


def now():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner: Mapped[str] = mapped_column(String, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String, unique=True)
    proposal: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default="pending")
    progress: Mapped[str] = mapped_column(String, default="queued")
    policy_version: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    decision: Mapped[dict | None] = mapped_column(JSON)
    active_attempt: Mapped[str | None] = mapped_column(String)


class Job(Base):
    __tablename__ = "jobs"
    ticket_id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sandbox_id: Mapped[str | None] = mapped_column(String)
    cleanup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(String, index=True)
    attempt_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    kind: Mapped[str] = mapped_column(String)
    details: Mapped[dict] = mapped_column(JSON)


class Policy(Base):
    __tablename__ = "policies"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    corpus: Mapped[str] = mapped_column(String, index=True)
    document_id: Mapped[str] = mapped_column(String)
    section: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(
        JSON().with_variant(Vector(384), "postgresql")
    )


url = settings().database_url or "sqlite:///./red-compass.db"
engine = create_engine(
    url,
    connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    pool_pre_ping=True,
)
Session = sessionmaker(engine, expire_on_commit=False)


def initialize():
    if engine.dialect.name == "postgresql":
        from sqlalchemy import text

        with engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    Base.metadata.create_all(engine)


def session():
    with Session() as db:
        yield db
