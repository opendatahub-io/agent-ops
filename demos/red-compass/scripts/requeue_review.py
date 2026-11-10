"""Retry an interrupted review without changing its proposal, history or decision.

Run inside the deployed worker with --ticket-id UUID. Existing attempt counts are
preserved; this queues one operator-requested attempt after automatic retries.
"""

import argparse
from uuid import UUID

from red_compass.db import Event, Job, Session, Ticket
from red_compass.worker import sdk
from red_compass.config import settings
from sqlalchemy import select


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticket-id", required=True, type=UUID)
    args = parser.parse_args()
    ticket_id = str(args.ticket_id)
    with Session.begin() as db:
        ticket = db.scalar(
            select(Ticket).where(Ticket.id == ticket_id).with_for_update()
        )
        job = db.get(Job, ticket_id)
        if not ticket or not job:
            raise RuntimeError("Request not found")
        if (
            ticket.status != "pending"
            or ticket.decision
            or ticket.progress != "interrupted"
            or job.state != "failed"
        ):
            raise RuntimeError("Only an interrupted, undecided request can be requeued")
        if job.sandbox_id:
            with sdk() as client:
                client.delete(
                    job.sandbox_id,
                    workspace=settings().openshell_workspace,
                    allow_missing=True,
                )
        db.add(
            Event(
                ticket_id=ticket_id,
                attempt_id="operator",
                kind="review_requeued",
                details={
                    "previous_attempts": job.attempts,
                    "reason": "Operator retry after operational failure",
                },
            )
        )
        ticket.active_attempt = None
        ticket.progress = "queued"
        job.state = "queued"
        job.sandbox_id = None
        job.lease_until = None
        job.cleanup_at = None
    print("Queued review:", ticket_id)


if __name__ == "__main__":
    main()
