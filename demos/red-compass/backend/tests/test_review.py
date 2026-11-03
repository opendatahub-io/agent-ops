import os

os.environ.update(
    DATABASE_URL="sqlite://",
    SESSION_SECRET="test-session-secret-32-characters-minimum",
    AGENT_TOKEN_SIGNING_SECRET="test-agent-secret-32-characters-minimum",
)
from types import SimpleNamespace

from fastapi.testclient import TestClient
from red_compass import auth, db
from red_compass.agent import run
import httpx
import pytest
from red_compass.api import app
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool


def setup_function():
    db.engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    db.Session.configure(bind=db.engine)
    db.initialize()


def proposal():
    return {
        "title": "Repair the lantern",
        "parcel_id": "RC-101",
        "work_type": "repair",
        "start_date": "2027-07-01",
        "end_date": "2027-07-02",
        "height_m": 2,
        "footprint_m2": 12,
        "materials": "stone",
        "colour": "grey",
        "intended_use": "public light",
        "narrative": "Preserve the outward form and historic public lighting function.",
    }


@pytest.mark.parametrize("fallback", [False, True])
def test_ownership_idempotency_and_stale_decision(fallback, caplog):
    with TestClient(app) as client:
        client.post("/api/session", json={"customer": "alex"})
        response = client.post(
            "/api/requests", json=proposal(), headers={"Idempotency-Key": "same"}
        )
        assert response.status_code == 202
        ticket_id = response.json()["id"]
        assert (
            client.post(
                "/api/requests", json=proposal(), headers={"Idempotency-Key": "same"}
            ).json()["id"]
            == ticket_id
        )
        client.post("/api/session", json={"customer": "sam"})
        assert client.get(f"/api/requests/{ticket_id}").status_code == 404
        assert client.get("/api/requests").json() == []
        with db.Session.begin() as session:
            ticket = session.get(db.Ticket, ticket_id)
            ticket.active_attempt = "current"
            session.add(
                db.Policy(
                    key="demo-2026-01:RC-BASE-01:1.3",
                    corpus="demo-2026-01",
                    document_id="RC-BASE-01",
                    section="1.3",
                    title="Base",
                    text="Insufficient evidence",
                    embedding=[0.0] * 384,
                )
            )

        def decision(attempt):
            return {
                "attempt_id": attempt,
                "decision": "deny",
                "summary": "Required evidence has not been established.",
                "reasons": [
                    {
                        "explanation": "A required policy condition is unresolved.",
                        "policy_citations": [
                            {"document_id": "RC-BASE-01", "section": "1.3"}
                        ],
                    }
                ],
                "policy_version": "demo-2026-01",
                "decision_origin": "iteration_limit_fallback" if fallback else "model",
                "termination_reason": "iteration_limit"
                if fallback
                else "early_decision",
            }

        stale = {
            "Authorization": "Bearer "
            + auth.issue(ticket_id, "agent", attempt_id="old")
        }
        assert (
            client.post(
                f"/agent/requests/{ticket_id}/decision",
                json=decision("old"),
                headers=stale,
            ).status_code
            == 409
        )
        current = {
            "Authorization": "Bearer "
            + auth.issue(ticket_id, "agent", attempt_id="current")
        }
        assert (
            client.post(
                f"/agent/requests/{ticket_id}/decision",
                json=decision("current"),
                headers=current,
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/agent/requests/{ticket_id}/decision",
                json=decision("current"),
                headers=current,
            ).status_code
            == 200
        )
        changed = decision("current")
        changed["decision"] = "approve"
        assert (
            client.post(
                f"/agent/requests/{ticket_id}/decision", json=changed, headers=current
            ).status_code
            == 409
        )

        warnings = [
            record
            for record in caplog.records
            if "Review fallback recorded" in record.message
        ]
        assert len(warnings) == int(fallback)
        if fallback:
            assert f"ticket_id={ticket_id}" in warnings[0].message
            assert "attempt_id=current" in warnings[0].message


class Block:
    def __init__(self, name):
        self.type = "tool_use"
        self.id = "call"
        self.name = name
        self.input = {}

    def model_dump(self, **kwargs):
        return vars(self)


class FakeTools:
    accepted = False
    calls = []

    def __init__(self):
        self.calls = []
        self.http = SimpleNamespace(
            post=lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None)
        )

    def call(self, name, args, final=False):
        self.calls.append(name)
        if name == "get_request":
            return {"precedence": ["base"]}
        if name == "submit_decision":
            self.accepted = True
        return {}

    def fallback(self):
        self.accepted = True
        self.calls.append("fallback")


@pytest.mark.parametrize("thinking_mode", ["default", "disabled"])
def test_early_decision_stops_remaining_tools(thinking_mode, monkeypatch):
    from red_compass.config import settings

    monkeypatch.setattr(settings(), "anthropic_thinking_mode", thinking_mode)
    tools = FakeTools()
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            content=[Block("submit_decision"), Block("lookup_parcel")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    result = run(SimpleNamespace(messages=SimpleNamespace(create=create)), tools)
    assert result["turns"] == 1
    assert len(calls) == 1
    assert calls[0].get("thinking") == (
        {"type": "disabled"} if thinking_mode == "disabled" else None
    )
    assert tools.calls == ["get_request", "search_policies", "submit_decision"]


def test_final_turn_is_decision_only_and_bounded():
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            content=[], usage=SimpleNamespace(input_tokens=1, output_tokens=1)
        )

    tools = FakeTools()
    result = run(
        SimpleNamespace(messages=SimpleNamespace(create=create)), tools, timeout=300
    )
    assert len(calls) == 30 and result["turns"] == 30
    assert [x["name"] for x in calls[-1]["tools"]] == ["submit_decision"]
    assert calls[-1]["tool_choice"]["name"] == "submit_decision"
    assert tools.calls[-1] == "fallback"


def test_service_failure_does_not_trigger_denial_fallback():
    tools = FakeTools()
    original = tools.call

    def call(name, args, final=False):
        if name != "submit_decision":
            return original(name, args, final)
        response = httpx.Response(
            503, request=httpx.Request("POST", "http://api/decision")
        )
        response.raise_for_status()

    tools.call = call
    model = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                content=[Block("submit_decision")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )
        )
    )
    with pytest.raises(httpx.HTTPStatusError):
        run(model, tools, max_turns=1)
    assert "fallback" not in tools.calls


def test_long_narrative_bounds_search_without_truncating_review():
    tools = FakeTools()
    original = tools.call
    narrative = "Preserve the original lantern. " * 300
    searches = []

    def call(name, args, final=False):
        if name == "get_request":
            return {"precedence": ["base"], "proposal": {"narrative": narrative}}
        if name == "search_policies":
            searches.append(args["query"])
        return original(name, args, final)

    def create(**kwargs):
        import json

        assert (
            json.loads(kwargs["messages"][0]["content"])["proposal"]["narrative"]
            == narrative
        )
        return SimpleNamespace(
            content=[Block("submit_decision")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    tools.call = call
    run(SimpleNamespace(messages=SimpleNamespace(create=create)), tools)
    assert searches == [narrative[:2000]]


def test_deadline_after_final_response_does_not_deny(monkeypatch):
    from red_compass import agent

    clock = [0]
    monkeypatch.setattr(agent.time, "monotonic", lambda: clock[0])
    tools = FakeTools()

    def create(**kwargs):
        clock[0] = 301
        return SimpleNamespace(
            content=[], usage=SimpleNamespace(input_tokens=1, output_tokens=1)
        )

    with pytest.raises(TimeoutError):
        run(
            SimpleNamespace(messages=SimpleNamespace(create=create)), tools, max_turns=1
        )
    assert "fallback" not in tools.calls


@pytest.mark.parametrize("http_error", [False, True])
def test_validation_logs_exclude_customer_data_and_secrets(http_error, capsys):
    tools = FakeTools()
    original = tools.call
    private = "PRIVATE_CUSTOMER_TEXT_AND_API_KEY"

    def call(name, args, final=False):
        if name != "lookup_parcel":
            return original(name, args, final)
        if http_error:
            httpx.Response(
                422, text=private, request=httpx.Request("GET", "http://api/evidence")
            ).raise_for_status()
        raise ValueError(private)

    turn = [0]

    def create(**kwargs):
        turn[0] += 1
        return SimpleNamespace(
            content=[Block("lookup_parcel" if turn[0] == 1 else "submit_decision")],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    tools.call = call
    run(SimpleNamespace(messages=SimpleNamespace(create=create)), tools)
    logs = capsys.readouterr().out
    assert private not in logs
    assert "error_type" in logs
    assert ('"status_code": 422' in logs) == http_error


@pytest.mark.parametrize("parcel", [None, 123, [], {}])
def test_invalid_parcel_type_is_recoverable(parcel):
    from red_compass.agent import Tools

    tools = Tools("http://api", "token", "ticket", "attempt", "corpus", client=object())
    with pytest.raises(ValueError, match="Invalid parcel ID"):
        tools.call("lookup_parcel", {"parcel_id": parcel})
