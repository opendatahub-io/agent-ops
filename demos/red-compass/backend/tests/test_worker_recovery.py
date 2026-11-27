from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

from red_compass import db, worker


@pytest.fixture
def store(monkeypatch):
    previous = db.Session.kw["bind"]
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    db.Session.configure(bind=engine)
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(
        worker,
        "settings",
        lambda: SimpleNamespace(
            max_review_retries=1,
            sandbox_retention_seconds=600,
            openshell_workspace="default",
            agent_timeout_seconds=300,
        ),
    )
    with db.Session.begin() as session:
        session.add(
            db.Ticket(
                id="ticket",
                owner="alex",
                proposal={},
                policy_version="demo",
                status="pending",
                progress="reviewing",
                active_attempt="attempt",
            )
        )
        session.add(
            db.Job(
                ticket_id="ticket",
                state="running",
                attempts=1,
                sandbox_id="old-sandbox",
                lease_until=db.now() + timedelta(hours=1),
            )
        )
    yield
    db.Session.configure(bind=previous)
    engine.dispose()


def client():
    deleted = []
    return SimpleNamespace(
        delete=lambda name, **kwargs: deleted.append(name), deleted=deleted
    )


def test_known_sandbox_error_retries_before_lease_expiry(store, monkeypatch):
    monkeypatch.setattr(worker, "sandbox_failed", lambda *args: True)
    gateway = client()
    worker.reconcile(gateway)
    with db.Session() as session:
        ticket = session.get(db.Ticket, "ticket")
        job = session.get(db.Job, "ticket")
        event = session.scalar(select(db.Event).where(db.Event.kind == "review_failed"))
        assert ticket.status == "pending" and ticket.decision is None
        assert ticket.progress == "retrying" and ticket.active_attempt is None
        assert job.state == "queued" and job.sandbox_id is None
        assert (
            event.details["reason"] == "sandbox_error" and event.details["will_retry"]
        )
    assert gateway.deleted == ["old-sandbox"]
    assert worker.claim()[0] == "ticket"


def test_running_sandbox_keeps_its_lease(store, monkeypatch):
    monkeypatch.setattr(worker, "sandbox_failed", lambda *args: False)
    gateway = client()
    worker.reconcile(gateway)
    with db.Session() as session:
        assert session.get(db.Job, "ticket").state == "running"
        assert session.get(db.Ticket, "ticket").active_attempt == "attempt"
    assert not gateway.deleted


def test_lease_expiry_remains_a_recovery_fallback(store, monkeypatch):
    monkeypatch.setattr(worker, "sandbox_failed", lambda *args: False)
    with db.Session.begin() as session:
        session.get(db.Job, "ticket").lease_until = db.now() - timedelta(seconds=1)
    worker.reconcile(client())
    with db.Session() as session:
        assert session.get(db.Job, "ticket").state == "queued"
        event = session.scalar(select(db.Event))
        assert event.details["reason"] == "lease_expired"


def test_exhausted_retries_interrupt_without_business_denial(store, monkeypatch):
    monkeypatch.setattr(worker, "sandbox_failed", lambda *args: True)
    with db.Session.begin() as session:
        session.get(db.Job, "ticket").attempts = 2
    gateway = client()
    worker.reconcile(gateway)
    with db.Session() as session:
        ticket = session.get(db.Ticket, "ticket")
        job = session.get(db.Job, "ticket")
        assert ticket.status == "pending" and ticket.decision is None
        assert ticket.progress == "interrupted" and ticket.active_attempt is None
        assert job.state == "failed" and job.cleanup_at is not None
        assert job.sandbox_id == "old-sandbox"
    assert not gateway.deleted  # Retained for operator inspection.


def test_cleanup_failure_preserves_reference_and_revokes_attempt(
    store, monkeypatch, caplog
):
    monkeypatch.setattr(worker, "sandbox_failed", lambda *args: True)
    gateway = client()

    def fail(*args, **kwargs):
        raise RuntimeError("private SDK exception")

    gateway.delete = fail
    worker.reconcile(gateway)
    with db.Session() as session:
        assert session.get(db.Ticket, "ticket").active_attempt is None
        job = session.get(db.Job, "ticket")
        assert job.state == "queued" and job.sandbox_id == "old-sandbox"
    assert worker.claim() is None
    assert "private SDK exception" not in caplog.text
    worker.reconcile(client())
    assert worker.claim()[0] == "ticket"
    with db.Session() as session:
        assert (
            len(
                session.scalars(
                    select(db.Event).where(db.Event.kind == "review_failed")
                ).all()
            )
            == 1
        )


def test_decision_committed_during_status_lookup_is_preserved(store, monkeypatch):
    def complete(*args):
        with db.Session.begin() as session:
            ticket = session.get(db.Ticket, "ticket")
            ticket.status = "approved"
            ticket.progress = "complete"
            ticket.decision = {"decision": "approve"}
            session.get(db.Job, "ticket").state = "done"
        return True

    monkeypatch.setattr(worker, "sandbox_failed", complete)
    gateway = client()
    worker.reconcile(gateway)
    with db.Session() as session:
        ticket = session.get(db.Ticket, "ticket")
        assert ticket.status == "approved" and ticket.progress == "complete"
        assert ticket.decision == {"decision": "approve"}
        assert session.get(db.Job, "ticket").state == "done"
        assert not session.scalars(select(db.Event)).all()
    assert not gateway.deleted


@pytest.mark.parametrize("hours", [-8, 0, 5.5])
def test_deadline_preserves_aware_timestamp_instant(hours):
    from datetime import datetime, timezone

    instant = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    offset = timezone(timedelta(hours=hours))
    assert worker.utc_deadline(instant.astimezone(offset)) == instant
    assert worker.utc_deadline(instant.replace(tzinfo=None)) == instant
