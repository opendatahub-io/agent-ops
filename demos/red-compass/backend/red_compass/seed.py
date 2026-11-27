"""Idempotent initial demo requests; all outcomes come from real queued reviews."""

import json
from importlib.resources import files
import uuid
from .config import settings
from .db import Session, Ticket, Job, initialize
from .schemas import Proposal


def seed_requests():
    initialize()
    created = 0
    cases = json.loads(files("red_compass").joinpath("seed_data.json").read_text())
    with Session.begin() as db:
        for case in cases:
            key = f"red-compass:seed-v1:{settings().policy_version}:{case['key']}"
            ticket_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))
            if db.get(Ticket, ticket_id):
                continue
            payload = Proposal.model_validate(case["proposal"]).model_dump(mode="json")
            db.add(
                Ticket(
                    id=ticket_id,
                    owner=case["owner"],
                    idempotency_key=key,
                    proposal=payload,
                    policy_version=settings().policy_version,
                )
            )
            db.add(Job(ticket_id=ticket_id))
            created += 1
    return created


if __name__ == "__main__":
    print(
        f"Created {seed_requests()} initial requests; existing seed requests preserved."
    )
