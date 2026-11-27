from types import SimpleNamespace

import pytest

from red_compass import worker


def test_sandbox_policy_allows_only_internal_tracking_endpoint(monkeypatch):
    pytest.importorskip("grpc")
    pytest.importorskip("cloudpickle")
    pytest.importorskip("google.rpc.status_pb2")
    # Exercise the pinned SDK's actual protobuf schema without a gateway.
    pytest.importorskip("openshell")
    monkeypatch.setenv("AGENT_IMAGE", "agent:test")
    cfg = SimpleNamespace(
        api_base_url="http://api.red-compass.svc.cluster.local:8000",
        mlflow_tracking_uri="http://mlflow-tracking.red-compass.svc.cluster.local:5000",
        mlflow_experiment_name="red-compass-reviews",
        anthropic_base_url="https://inference.example.org",
        anthropic_model="demo-model",
        anthropic_thinking_mode="disabled",
        agent_max_turns=30,
        agent_max_tools_per_turn=3,
        agent_timeout_seconds=300,
        openshell_provider="demo-model",
        openshell_workspace="default",
    )
    monkeypatch.setattr(worker, "settings", lambda: cfg)
    monkeypatch.setattr(worker, "issue", lambda *args, **kwargs: "scoped-test-token")
    captured = {}
    client = SimpleNamespace(create=lambda **kwargs: captured.update(kwargs))
    worker.launch(client, "ticket-1", "attempt-1", "demo-corpus", "sandbox-1")
    spec = captured["spec"]
    assert spec.environment["MLFLOW_TRACKING_URI"] == cfg.mlflow_tracking_uri
    assert spec.environment["SANDBOX_NAME"] == "sandbox-1"
    assert spec.environment["AGENT_MAX_TOOLS_PER_TURN"] == "3"
    assert spec.environment["MLFLOW_HTTP_REQUEST_MAX_RETRIES"] == "1"
    assert spec.environment["ANTHROPIC_THINKING_MODE"] == "disabled"
    assert "ANTHROPIC_API_KEY" not in spec.environment
    policy = spec.policy.network_policies["business"]
    assert policy.binaries[0].path == "/usr/bin/python3.12"
    endpoint = next(
        e for e in policy.endpoints if e.host.startswith("mlflow-tracking.")
    )
    assert endpoint.port == 5000
    assert endpoint.allowed_ips == ["172.30.0.0/16"]
    assert not any("example.org" in e.host for e in policy.endpoints)


def test_trace_transport_settings_are_accepted_by_mlflow(monkeypatch):
    mlflow_env = pytest.importorskip("mlflow.environment_variables")
    from red_compass.config import settings

    pytest.importorskip("openshell")
    pytest.importorskip("grpc")
    pytest.importorskip("cloudpickle")
    pytest.importorskip("google.rpc.status_pb2")
    monkeypatch.setenv("AGENT_IMAGE", "agent:test")
    monkeypatch.setattr(worker, "settings", settings)
    monkeypatch.setattr(worker, "issue", lambda *args, **kwargs: "scoped-test-token")
    captured = {}
    worker.launch(
        SimpleNamespace(create=lambda **kwargs: captured.update(kwargs)),
        "ticket",
        "attempt",
        "demo-corpus",
        "sandbox",
    )
    environment = captured["spec"].environment
    for key in (
        "MLFLOW_HTTP_REQUEST_TIMEOUT",
        "MLFLOW_HTTP_REQUEST_MAX_RETRIES",
        "MLFLOW_HTTP_REQUEST_BACKOFF_FACTOR",
    ):
        monkeypatch.setenv(key, environment[key])
        assert getattr(mlflow_env, key).get() >= 0


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("SANDBOX_PHASE_ERROR", True),
        ("SANDBOX_PHASE_READY", False),
        ("SANDBOX_PHASE_STOPPED", False),
        ("SANDBOX_PHASE_COMPLETED", False),
    ],
)
def test_gateway_error_phase_detection(monkeypatch, phase, expected):
    pytest.importorskip("grpc")
    pytest.importorskip("cloudpickle")
    pytest.importorskip("google.rpc.status_pb2")
    pytest.importorskip("openshell")
    from openshell._proto import openshell_pb2 as pb

    gateway = SimpleNamespace(
        get=lambda *args, **kwargs: SimpleNamespace(phase=getattr(pb, phase))
    )
    assert worker.sandbox_failed(gateway, "sandbox", "default") is expected
