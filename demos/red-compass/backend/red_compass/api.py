import hashlib
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import auth, retrieval
from .config import settings
from .db import Event, Job, Policy, Ticket, initialize, session
from .evidence import lookup
from .schemas import Decision, Proposal

log = logging.getLogger("red_compass.api")


@asynccontextmanager
async def lifespan(app):
    auth.secret("session_secret")
    auth.secret("agent_token_signing_secret")
    initialize()
    yield


app = FastAPI(title="Red Compass", lifespan=lifespan)


def ticket_view(ticket):
    return {
        key: getattr(ticket, key)
        for key in [
            "id",
            "proposal",
            "status",
            "progress",
            "policy_version",
            "created_at",
            "decision",
            "active_attempt",
        ]
    }


def owned(db, ticket_id, owner):
    ticket = db.get(Ticket, ticket_id)
    if not ticket or ticket.owner != owner:
        raise HTTPException(404, "Request not found")
    return ticket


def active(db, ticket_id, claims, lock=False):
    query = select(Ticket).where(Ticket.id == ticket_id)
    if lock:
        query = query.with_for_update()
    ticket = db.scalar(query)
    if not ticket or claims["sub"] != ticket_id:
        raise HTTPException(403, "Ticket outside credential scope")
    if ticket.active_attempt != claims["attempt_id"]:
        raise HTTPException(409, "Review attempt is no longer active")
    return ticket


@app.get("/healthz")
def health():
    return {"status": "ok"}


class Login(BaseModel):
    customer: str


@app.post("/api/session")
def login(payload: Login, response: Response, request: Request):
    if payload.customer not in ["alex", "sam"]:
        raise HTTPException(400, "Unknown demo customer")
    response.set_cookie(
        "rc_session",
        auth.issue(payload.customer, "customer", 480),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
        max_age=28800,
    )
    return {"customer": payload.customer}


@app.get("/api/session")
def me(owner=Depends(auth.customer)):
    return {"customer": owner}


@app.post("/api/requests", status_code=202)
def submit(
    payload: Proposal,
    owner=Depends(auth.customer),
    db=Depends(session),
    idempotency_key: str | None = Header(default=None),
):
    if payload.end_date < payload.start_date:
        raise HTTPException(422, "End date must follow start date")
    data = payload.model_dump(mode="json")
    key = (
        hashlib.sha256(f"{owner}:{idempotency_key}".encode()).hexdigest()
        if idempotency_key
        else None
    )

    def existing():
        row = db.scalar(select(Ticket).where(Ticket.idempotency_key == key))
        if row and row.proposal != data:
            raise HTTPException(
                409, "Idempotency key already used for another proposal"
            )
        return ticket_view(row) if row else None

    if key and (result := existing()):
        return result
    ticket = Ticket(
        id=str(uuid.uuid4()),
        owner=owner,
        idempotency_key=key,
        proposal=data,
        policy_version=settings().policy_version,
    )
    db.add(ticket)
    db.add(Job(ticket_id=ticket.id))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if key and (result := existing()):
            return result
        raise
    return ticket_view(ticket)


@app.get("/api/requests")
def tickets(owner=Depends(auth.customer), db=Depends(session)):
    return [
        ticket_view(t)
        for t in db.scalars(
            select(Ticket)
            .where(Ticket.owner == owner)
            .order_by(Ticket.created_at.desc())
        )
    ]


@app.get("/api/requests/{ticket_id}")
def detail(ticket_id: str, owner=Depends(auth.customer), db=Depends(session)):
    ticket = owned(db, ticket_id, owner)
    result = ticket_view(ticket)
    result["events"] = [
        {"kind": e.kind, "details": e.details, "created_at": e.created_at}
        for e in db.scalars(
            select(Event).where(Event.ticket_id == ticket_id).order_by(Event.id)
        )
    ]
    return result


@app.get("/agent/requests/{ticket_id}")
def agent_request(ticket_id: str, claims=Depends(auth.agent), db=Depends(session)):
    ticket = active(db, ticket_id, claims)
    rows = db.scalars(
        select(Policy).where(Policy.corpus == ticket.policy_version)
    ).all()
    return {
        **ticket_view(ticket),
        "manifest": [
            {"document_id": p.document_id, "section": p.section, "title": p.title}
            for p in rows
        ],
        "precedence": [
            retrieval.public(p) for p in rows if p.document_id == "RC-BASE-01"
        ],
    }


class Search(BaseModel):
    query: str = Field(min_length=2, max_length=2000)


@app.post("/agent/policies/search")
def search(payload: Search, claims=Depends(auth.agent), db=Depends(session)):
    ticket = active(db, claims["sub"], claims)
    return retrieval.search(db, ticket.policy_version, payload.query)


@app.get("/agent/policies/{document_id}")
def document(document_id: str, claims=Depends(auth.agent), db=Depends(session)):
    ticket = active(db, claims["sub"], claims)
    rows = db.scalars(
        select(Policy).where(
            Policy.corpus == ticket.policy_version, Policy.document_id == document_id
        )
    ).all()
    if not rows:
        raise HTTPException(404, "Policy not found")
    return [retrieval.public(p) for p in rows]


@app.get("/agent/evidence/{kind}/{parcel_id}")
def evidence(
    kind: str, parcel_id: str, claims=Depends(auth.agent), db=Depends(session)
):
    ticket = active(db, claims["sub"], claims)
    if (
        kind not in ["parcels", "heritage", "environment"]
        or parcel_id != ticket.proposal["parcel_id"]
    ):
        raise HTTPException(403, "Evidence outside review scope")
    result = lookup(kind, parcel_id)
    db.add(
        Event(
            ticket_id=ticket.id,
            attempt_id=claims["attempt_id"],
            kind="evidence",
            details=result,
        )
    )
    db.commit()
    return result


class Audit(BaseModel):
    turn: int = Field(ge=1, le=30)
    tool: str = Field(max_length=100)
    status: str = Field(max_length=50)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)


@app.post("/agent/events")
def event(payload: Audit, claims=Depends(auth.agent), db=Depends(session)):
    active(db, claims["sub"], claims)
    db.add(
        Event(
            ticket_id=claims["sub"],
            attempt_id=claims["attempt_id"],
            kind="tool",
            details=payload.model_dump(),
        )
    )
    db.commit()
    return {"ok": True}


@app.post("/agent/requests/{ticket_id}/decision")
def decide(
    ticket_id: str, payload: Decision, claims=Depends(auth.agent), db=Depends(session)
):
    ticket = active(db, ticket_id, claims, lock=True)
    data = payload.model_dump()
    if (
        payload.attempt_id != claims["attempt_id"]
        or payload.policy_version != ticket.policy_version
    ):
        raise HTTPException(409, "Attempt or policy version mismatch")
    if ticket.decision:
        if ticket.decision == data:
            return ticket_view(ticket)
        raise HTTPException(409, "Decision already recorded")
    for reason in payload.reasons:
        for cite in reason.policy_citations:
            if cite.version != "1" or not db.get(
                Policy, f"{ticket.policy_version}:{cite.document_id}:{cite.section}"
            ):
                raise HTTPException(422, "Unknown policy citation")
        fetched = {
            e.details["id"]
            for e in db.scalars(
                select(Event).where(
                    Event.ticket_id == ticket.id,
                    Event.attempt_id == claims["attempt_id"],
                    Event.kind == "evidence",
                )
            )
        }
        if not set(reason.evidence_ids) <= fetched:
            raise HTTPException(422, "Evidence must be fetched before citing it")
    ticket.decision = data
    ticket.status = "approved" if payload.decision == "approve" else "denied"
    ticket.progress = "complete"
    job = db.get(Job, ticket.id)
    job.state = "done"
    db.add(
        Event(
            ticket_id=ticket.id,
            attempt_id=claims["attempt_id"],
            kind="decision",
            details={
                "decision": payload.decision,
                "termination_reason": payload.termination_reason,
                "decision_origin": payload.decision_origin,
            },
        )
    )
    db.commit()
    if payload.decision_origin == "iteration_limit_fallback":
        log.warning(
            "Review fallback recorded ticket_id=%s attempt_id=%s "
            "decision=deny decision_origin=iteration_limit_fallback "
            "termination_reason=iteration_limit",
            ticket.id,
            claims["attempt_id"],
        )
    return ticket_view(ticket)
