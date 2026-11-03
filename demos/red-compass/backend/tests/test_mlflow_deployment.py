"""Check the generated MLflow access boundary and persistent-storage wiring."""

import importlib.util
from pathlib import Path

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "mlflow_resources", ROOT / "scripts/mlflow_resources.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_trace_viewer_exposes_only_oauth_and_keeps_tracking_internal():
    template = list(yaml.safe_load_all((ROOT / "deploy/dashboard.yaml").read_text()))
    resources = module.resources(template, "traces.example.org", "proxy:test")
    assert resources[0]["kind"] == "PersistentVolumeClaim"
    deployment = next(r for r in resources if r["kind"] == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    backend, proxy = pod["containers"]
    assert pod["automountServiceAccountToken"] is False
    assert backend["volumeMounts"] == [{"name": "data", "mountPath": "/data"}]
    assert backend["resources"]["limits"]["memory"] == "2Gi"
    assert "envFrom" not in backend
    assert (
        "mlflow-tracking.red-compass.svc.cluster.local:5000"
        in backend["env"][0]["value"]
    )
    assert proxy["image"] == "proxy:test"
    assert "--upstream=http://127.0.0.1:5000" in proxy["args"]
    assert "--redirect-url=https://traces.example.org/oauth/callback" in proxy["args"]
    assert any(arg.startswith("--openshift-sar=") for arg in proxy["args"])
    assert all(m["name"] != "oauth-service-account" for m in backend["volumeMounts"])
    assert any(m["name"] == "oauth-service-account" for m in proxy["volumeMounts"])
    route = next(r for r in resources if r["kind"] == "Route")
    assert route["spec"]["to"]["name"] == "mlflow"
    service = next(
        r
        for r in resources
        if r["kind"] == "Service" and r["metadata"]["name"] == "mlflow"
    )
    assert service["spec"]["ports"][0]["targetPort"] == 8888
    policy = next(r for r in resources if r["kind"] == "NetworkPolicy")
    rules = policy["spec"]["ingress"]
    assert rules[0]["ports"] == [{"protocol": "TCP", "port": 8888}]
    assert rules[1]["ports"] == [{"protocol": "TCP", "port": 5000}]
    assert rules[1]["from"] == [
        {
            "namespaceSelector": {
                "matchLabels": {"kubernetes.io/metadata.name": "openshell"}
            }
        }
    ]
    assert len([r for r in resources if r["kind"] == "Route"]) == 1
    assert len([r for r in resources if r["kind"] == "PersistentVolumeClaim"]) == 1
    assert "openshell-client-tls" not in str(resources)
    assert template[0]["metadata"]["namespace"] == "openshell"


@pytest.mark.parametrize("unprotected", [None, "dashboard", "mlflow"])
def test_routes_only_verification_never_accesses_seed_requests(
    monkeypatch, unprotected
):
    spec = importlib.util.spec_from_file_location(
        "verify_deployment", ROOT / "scripts/verify_deployment.py"
    )
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    monkeypatch.setattr(
        verifier,
        "route",
        lambda ns, name: {
            "red-compass": "https://portal.example.org",
            "openshell-dashboard": "https://dashboard.example.org",
            "mlflow": "https://mlflow.example.org",
        }[name],
    )
    monkeypatch.setattr("sys.argv", ["verify_deployment.py", "--routes-only"])
    calls = []

    def respond(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.path in {
            "/",
            "/api/v1/gateway/compatibility",
            "/api/2.0/mlflow/experiments/search",
        }
        if (
            request.url.host == "portal.example.org"
            or request.url.host == f"{unprotected}.example.org"
        ):
            return httpx.Response(200)
        return httpx.Response(
            302, headers={"location": "https://oauth.example.org/login"}
        )

    client = httpx.Client
    monkeypatch.setattr(
        verifier.httpx,
        "Client",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    if unprotected:
        with pytest.raises(RuntimeError, match="did not redirect"):
            verifier.main()
    else:
        verifier.main()
        assert len(calls) == 3


def test_published_sdk_versions_match_release_configuration():
    import json
    import tomllib

    release = json.loads((ROOT / "deploy/openshell-release.json").read_text())
    requirement = "openshell==" + release["sdk_version"]
    assert (
        requirement
        in tomllib.loads((ROOT / "backend/pyproject.toml").read_text())[
            "dependency-groups"
        ]["dev"]
    )
    assert requirement in (ROOT / "deploy/Containerfile.worker").read_text()


def test_published_helm_chart_image_overrides():
    import json
    import os
    import subprocess

    if os.environ.get("OPENSHELL_HELM_TEST") != "1":
        pytest.skip("Set OPENSHELL_HELM_TEST=1 to render the published OCI chart")
    release = json.loads((ROOT / "deploy/openshell-release.json").read_text())
    result = subprocess.check_output(
        [
            "helm",
            "template",
            "openshell",
            release["chart"],
            "--version",
            release["chart_version"],
            "--namespace",
            "openshell",
            "-f",
            str(ROOT / "deploy/openshell-values.yaml"),
            "--set",
            "agentSandbox.preflight.enabled=false",
        ],
        text=True,
    )
    documents = list(yaml.safe_load_all(result))
    serialized = json.dumps(documents)
    # Supervisor and sandbox images live in gateway TOML, not standalone pods.
    for component in ("gateway", "supervisor", "sandbox"):
        assert (
            f"quay.io/opendatahub/odh-openshell-{component}:{release['runtime_release']}"
            in serialized
        )
    dashboard = list(yaml.safe_load_all((ROOT / "deploy/dashboard.yaml").read_text()))
    deployment = next(d for d in dashboard if d["kind"] == "Deployment")
    image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    assert (
        image
        == "quay.io/opendatahub/odh-openshell-dashboard:" + release["dashboard_tag"]
    )
