from red_compass.seed import seed_requests
from red_compass import db
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool


def test_initial_seed_is_queued_and_idempotent():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    previous = db.engine
    db.engine = engine
    db.Session.configure(bind=engine)
    try:
        assert seed_requests() == 6
        with db.Session.begin() as session:
            tickets = session.scalars(select(db.Ticket)).all()
            assert {t.owner for t in tickets} == {"alex", "sam"}
            assert all(t.status == "pending" and t.decision is None for t in tickets)
            assert len(session.scalars(select(db.Job)).all()) == 6
            first = tickets[0]
            first.status = "approved"
            first.decision = {"summary": "Existing decision must survive reruns"}
            ticket_id = first.id
        assert seed_requests() == 0
        with db.Session() as session:
            assert (
                session.get(db.Ticket, ticket_id).decision["summary"]
                == "Existing decision must survive reruns"
            )
    finally:
        db.engine = previous
        db.Session.configure(bind=previous)
        engine.dispose()
