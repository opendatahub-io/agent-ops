"""Exercise actual MLflow persistence, redaction and failure isolation."""

import json
from types import SimpleNamespace

import httpx
import pytest

from red_compass.agent import Tools, run
from red_compass import tracing


@pytest.fixture
def tracking(monkeypatch, tmp_path):
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    monkeypatch.setenv("MLFLOW_ENABLE_TELEMETRY", "false")
    monkeypatch.setenv("MLFLOW_ENABLE_ASYNC_TRACE_LOGGING", "false")
    mlflow = pytest.importorskip("mlflow")
    pytest.importorskip(
        "alembic", reason="Install the tracing extra for persistence tests"
    )
    cfg = SimpleNamespace(
        mlflow_tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}",
        mlflow_experiment_name="review-traces",
    )
    monkeypatch.setattr(tracing, "settings", lambda: cfg)
    return mlflow


def review_model(fail=False):
    def create(**kwargs):
        if fail:
            raise RuntimeError("sensitive exception body")
        block = SimpleNamespace(
            type="tool_use",
            id="decision",
            name="submit_decision",
            input={
                "decision": "deny",
                "summary": "private applicant summary",
                "reasons": [
                    {
                        "explanation": "private reason",
                        "policy_citations": [
                            {
                                "document_id": "RC-BASE-01",
                                "section": "1.4",
                                "version": "1",
                            },
                        ],
                    }
                ],
            },
        )
        block.model_dump = lambda **kw: {
            "type": block.type,
            "id": block.id,
            "name": block.name,
            "input": block.input,
        }
        return SimpleNamespace(
            content=[block], usage=SimpleNamespace(input_tokens=10, output_tokens=5)
        )

    return SimpleNamespace(messages=SimpleNamespace(create=create))


def tools():
    def respond(request):
        if request.url.path.endswith("/search"):
            return httpx.Response(
                200,
                json=[
                    {
                        "document_id": "RC-BASE-01",
                        "section": "1.4",
                        "text": "private policy body",
                    }
                ],
            )
        if request.url.path.endswith("/decision"):
            return httpx.Response(200, json={"status": "denied"})
        return httpx.Response(
            200,
            json={
                "precedence": ["base"],
                "proposal": {"narrative": "private applicant narrative"},
            },
        )

    http = httpx.Client(base_url="http://api", transport=httpx.MockTransport(respond))
    return Tools(
        "http://api",
        "private-api-token",
        "ticket-1",
        "attempt-1",
        "demo-corpus",
        client=http,
    )


def test_review_trace_persists_linked_spans_without_private_content(tracking):
    result = run(review_model(), tools())
    assert result["turns"] == 1
    traces = tracking.search_traces(return_type="list")
    assert len(traces) == 1
    trace = traces[0]
    spans = {span.name: span for span in trace.data.spans}
    root = spans["construction_review"]
    assert trace.info.tags["ticket_id"] == "ticket-1"
    assert trace.info.tags["attempt_id"] == "attempt-1"
    assert set(spans) == {
        "construction_review",
        "get_request",
        "search_policies",
        "model_turn_1",
        "submit_decision",
    }
    assert all(
        span.parent_id == root.span_id
        for name, span in spans.items()
        if name != root.name
    )
    assert root.outputs["decision"] == "deny"
    assert (
        spans["model_turn_1"].attributes["mlflow.chat.tokenUsage"]["total_tokens"] == 15
    )
    assert (
        spans["submit_decision"].outputs["citations"][0]["document_id"] == "RC-BASE-01"
    )
    assert "private" not in json.dumps(trace.to_dict())


def test_export_failure_does_not_change_decision(tracking, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError("private credential in exporter exception")

    monkeypatch.setattr(tracking, "set_experiment", fail)
    reviewer_tools = tools()
    assert run(review_model(), reviewer_tools)["turns"] == 1
    assert reviewer_tools.accepted
    assert "initialization unavailable" in caplog.text
    assert "private credential" not in caplog.text


def test_model_failure_is_traced_without_recording_exception_body(tracking):
    with pytest.raises(RuntimeError, match="sensitive exception body"):
        run(review_model(fail=True), tools())
    trace = tracking.search_traces(return_type="list")[0]
    spans = {span.name: span for span in trace.data.spans}
    assert spans["model_turn_1"].status.status_code.value == "ERROR"
    assert spans["construction_review"].status.status_code.value == "ERROR"
    assert "sensitive exception body" not in json.dumps(trace.to_dict())


def test_iteration_limit_fallback_is_visible_in_trace(tracking):
    model = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                content=[],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )
        )
    )
    reviewer_tools = tools()
    result = run(model, reviewer_tools, max_turns=1)
    assert result["decision_origin"] == "iteration_limit_fallback"
    trace = tracking.search_traces(return_type="list")[0]
    spans = {span.name: span for span in trace.data.spans}
    assert spans["fallback"].outputs["decision"] == "deny"
    assert (
        spans["construction_review"].outputs["decision_origin"]
        == "iteration_limit_fallback"
    )
    assert spans["model_turn_1"].inputs["decision_only"] is True
