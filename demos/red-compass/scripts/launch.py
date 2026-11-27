"""Launch the complete seeded demo on the currently selected OpenShift cluster."""

import argparse
import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
RELEASE = json.loads((ROOT / "deploy/openshell-release.json").read_text())
NS = "red-compass"
COMPONENTS = ["agent", "api", "web", "worker", "database", "mlflow"]


def run(args, *, env=None, capture=False, data=None):
    result = subprocess.run(
        [str(x) for x in args],
        cwd=ROOT,
        env=env,
        input=data,
        text=True,
        capture_output=capture,
    )
    if result.returncode:
        # Do not include captured stdout/stderr: some operations handle Secrets.
        raise RuntimeError(
            f"Command failed ({result.returncode}): {Path(str(args[0])).name} {args[1]}"
        )
    return result.stdout if capture else None


def oc_json(*args, optional=False):
    result = subprocess.run(
        ["oc", *args, "-o", "json"], cwd=ROOT, text=True, capture_output=True
    )
    if result.returncode:
        if optional and ("NotFound" in result.stderr or "not found" in result.stderr):
            return None
        raise RuntimeError(f"Cannot read cluster resource: {' '.join(args[:4])}")
    return json.loads(result.stdout)


def apply(resource):
    run(["oc", "apply", "-f", "-"], data=json.dumps(resource), capture=True)


def wait_for(description, predicate, seconds=900):
    deadline = time.monotonic() + seconds
    last_notice = 0
    while time.monotonic() < deadline:
        if predicate():
            return
        if time.monotonic() - last_notice >= 25:
            print("Waiting for " + description + "…", flush=True)
            last_notice = time.monotonic()
        time.sleep(3)
    raise TimeoutError("Timed out waiting for " + description)


def read_manifest(python, relative, env):
    return json.loads(
        run(
            [
                python,
                "-c",
                "import sys,json,yaml; print(json.dumps(list(yaml.safe_load_all(open(sys.argv[1])))))",
                ROOT / relative,
            ],
            env=env,
            capture=True,
        )
    )


def ensure_operator(python, env):
    existing = oc_json(
        "-n",
        "agent-sandbox-system",
        "get",
        "deployment",
        "agent-sandbox-controller",
        optional=True,
    )
    if not existing:
        manifests = read_manifest(python, "deploy/operator/install.yaml", env)
        # Never add a second OperatorGroup to an existing operator namespace.
        apply(manifests[0])
        groups = oc_json("-n", "agent-sandbox-system", "get", "operatorgroups")
        if not groups["items"]:
            apply(manifests[1])
        apply(manifests[2])

        def approve_pinned_plan():
            sub = oc_json(
                "-n",
                "agent-sandbox-system",
                "get",
                "subscription",
                "agent-sandbox-operator",
            )
            ref = sub.get("status", {}).get("installPlanRef", {}).get("name")
            if not ref:
                return False
            plan = oc_json("-n", "agent-sandbox-system", "get", "installplan", ref)
            if "agent-sandbox-operator.v0.9.0" not in plan["spec"].get(
                "clusterServiceVersionNames", []
            ):
                raise RuntimeError(
                    "Catalog no longer offers the pinned Agent Sandbox Operator v0.9.0. No different version was approved."
                )
            if not plan["spec"].get("approved"):
                run(
                    [
                        "oc",
                        "-n",
                        "agent-sandbox-system",
                        "patch",
                        "installplan",
                        ref,
                        "--type=merge",
                        "-p",
                        '{"spec":{"approved":true}}',
                    ]
                )
            return True

        wait_for(
            "pinned Red Hat Agent Sandbox Operator install plan",
            approve_pinned_plan,
            600,
        )
        wait_for(
            "Agent Sandbox controller Deployment",
            lambda: (
                oc_json(
                    "-n",
                    "agent-sandbox-system",
                    "get",
                    "deployment",
                    "agent-sandbox-controller",
                    optional=True,
                )
                is not None
            ),
            600,
        )
    run(
        [
            "oc",
            "-n",
            "agent-sandbox-system",
            "rollout",
            "status",
            "deployment/agent-sandbox-controller",
            "--timeout=300s",
        ]
    )
    oc_json("get", "crd", "sandboxes.agents.x-k8s.io")


@contextmanager
def gateway_forward(env):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    logs = ROOT / ".cache"
    logs.mkdir(exist_ok=True)
    path = logs / "launch-gateway-forward.log"
    with path.open("w") as output:
        path.chmod(0o600)
        process = subprocess.Popen(
            [
                "oc",
                "-n",
                "openshell",
                "port-forward",
                "svc/openshell",
                f"{port}:8080",
                "--address=127.0.0.1",
            ],
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:

            def ready():
                if process.poll() is not None:
                    raise RuntimeError(
                        "Gateway setup port-forward failed; inspect .cache/launch-gateway-forward.log"
                    )
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                        return True
                except OSError:
                    return False

            wait_for("temporary gateway connection", ready, 45)
            yield f"127.0.0.1:{port}"
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Read-only cluster and release preflight; no deployment",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild even if source hashes and successful images are unchanged",
    )
    parser.add_argument(
        "--no-seed",
        action="store_true",
        help="Index policies but do not create initial customer requests",
    )
    args = parser.parse_args()
    for tool in ["oc", "helm", "uv"]:
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} is required on PATH")
    for relative in [
        "deploy/openshell-release.json",
        "backend/red_compass/seed_data.json",
    ]:
        if not (ROOT / relative).is_file():
            raise RuntimeError("Required asset missing: " + relative)
    context = run(["oc", "config", "current-context"], capture=True).strip()
    run(["oc", "whoami"])
    version = oc_json("get", "clusterversion", "version")["status"]["desired"][
        "version"
    ]
    if tuple(int(x) for x in version.split(".")[:2]) < (4, 19):
        raise RuntimeError(
            "OpenShift 4.19+ is required; this bundle was validated on 4.22.9 amd64"
        )
    nodes = oc_json("get", "nodes")["items"]
    if not any(n["status"]["nodeInfo"]["architecture"] == "amd64" for n in nodes):
        raise RuntimeError("This pinned demo bundle requires amd64 nodes")
    print(f"Target: {context}; OpenShift {version}", flush=True)
    print(
        f"OpenShell images: {RELEASE['runtime_release']}; chart/SDK: {RELEASE['chart_version']}; six seed scenarios",
        flush=True,
    )
    if args.check:
        print(
            "Read-only preflight passed. No cluster resources or local configuration changed."
        )
        return

    print("[1/8] Preparing configuration and deployment tools", flush=True)
    run([sys.executable, ROOT / "scripts/init_env.py"])
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "backend")
    tool_env = ROOT / ".deploy-venv"
    python = tool_env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        run(["uv", "venv", "--python", "3.12", tool_env])
    run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            python,
            "pydantic-settings==2.16.2",
            "pyyaml==6.0.3",
            "grpcio==1.85.0",
            "protobuf==7.36.2",
            "openshell==" + RELEASE["sdk_version"],
        ],
        env=env,
    )
    run(
        [
            python,
            "-c",
            "from red_compass.config import model_endpoint; model_endpoint()",
        ],
        env=env,
    )
    # Retain authoritative server-side secrets when rerunning from another checkout.
    existing = oc_json("-n", NS, "get", "secret", "application", optional=True)
    if existing:
        for key in [
            "DATABASE_PASSWORD",
            "SESSION_SECRET",
            "AGENT_TOKEN_SIGNING_SECRET",
        ]:
            env[key] = base64.b64decode(existing["data"][key]).decode()
        print("Existing application credentials retained", flush=True)

    print("[2/8] Ensuring Red Hat Agent Sandbox Operator", flush=True)
    ensure_operator(python, env)
    print("[3/8] Building application images on OpenShift", flush=True)
    command = [python, ROOT / "scripts/build_images.py"]
    if not args.rebuild:
        command.append("--skip-unchanged")
    run(command, env=env)

    def builds_ready():
        all_ready = True
        for component in COMPONENTS:
            config = oc_json("-n", NS, "get", "buildconfig", component)
            name = f"{component}-{config['status']['lastVersion']}"
            build = oc_json("-n", NS, "get", "build", name)
            phase = build.get("status", {}).get("phase")
            if phase in ["Failed", "Error", "Cancelled"]:
                raise RuntimeError(
                    f"Build {name} failed. Inspect: oc -n {NS} logs build/{name}"
                )
            all_ready = all_ready and phase == "Complete"
        return all_ready

    wait_for("all six image builds", builds_ready, 2400)

    print("[4/8] Deploying application and initializing the database", flush=True)
    run([python, ROOT / "scripts/deploy_app.py"], env=env)
    run(["oc", "-n", NS, "rollout", "status", "deployment/database", "--timeout=300s"])
    exists = run(
        [
            "oc",
            "-n",
            NS,
            "exec",
            "deployment/database",
            "--",
            "psql",
            "-U",
            "redcompass",
            "-d",
            "postgres",
            "-tAc",
            "SELECT 1 FROM pg_database WHERE datname='redcompass'",
        ],
        capture=True,
    ).strip()
    if exists != "1":
        run(
            [
                "oc",
                "-n",
                NS,
                "exec",
                "deployment/database",
                "--",
                "psql",
                "-U",
                "redcompass",
                "-d",
                "postgres",
                "-c",
                "CREATE DATABASE redcompass WITH ENCODING 'UTF8' TEMPLATE template0",
            ]
        )
    run(["oc", "-n", NS, "rollout", "restart", "deployment/api", "deployment/web"])
    for name in ["api", "web"]:
        run(
            [
                "oc",
                "-n",
                NS,
                "rollout",
                "status",
                "deployment/" + name,
                "--timeout=300s",
            ]
        )

    print("[5/8] Installing the published OpenShell Helm chart", flush=True)
    run(
        [
            "helm",
            "upgrade",
            "--install",
            "openshell",
            RELEASE["chart"],
            "--version",
            RELEASE["chart_version"],
            "--namespace",
            "openshell",
            "--create-namespace",
            "-f",
            ROOT / "deploy/openshell-values.yaml",
            "--timeout=5m",
        ]
    )
    run(
        [
            "oc",
            "-n",
            "openshell",
            "rollout",
            "status",
            "statefulset/openshell",
            "--timeout=300s",
        ]
    )
    run(
        [
            "oc",
            "-n",
            NS,
            "policy",
            "add-role-to-group",
            "system:image-puller",
            "system:serviceaccounts:openshell",
        ]
    )
    with gateway_forward(env) as address:
        run(
            [python, ROOT / "scripts/configure_gateway.py"],
            env={**env, "GATEWAY_SETUP_ADDRESS": address},
        )

    print("[6/8] Indexing policies and applying the initial seed", flush=True)
    job = read_manifest(python, "deploy/ingest-job.yaml", env)[0]
    name = "bootstrap-" + uuid.uuid4().hex[:10]
    job["metadata"]["name"] = name
    job["spec"]["ttlSecondsAfterFinished"] = 86400
    container = job["spec"]["template"]["spec"]["containers"][0]
    container["command"] = [
        "/opt/app-root/bin/python3.12",
        "-m",
        "red_compass.bootstrap",
    ]
    if args.no_seed:
        container["command"].append("--policies-only")
    apply(job)

    def bootstrap_ready():
        data = oc_json("-n", NS, "get", "job", name)
        conditions = data.get("status", {}).get("conditions", [])
        if any(c["type"] == "Failed" and c["status"] == "True" for c in conditions):
            raise RuntimeError(f"Bootstrap failed. Inspect: oc -n {NS} logs job/{name}")
        return any(
            c["type"] == "Complete" and c["status"] == "True" for c in conditions
        )

    wait_for("policy and request bootstrap Job", bootstrap_ready, 600)
    run(["oc", "-n", NS, "logs", "job/" + name, "--tail=4"])

    print("Preparing MLflow trace storage and authenticated viewer", flush=True)
    mlflow_exists = (
        oc_json("-n", NS, "get", "deployment", "mlflow", optional=True) is not None
    )
    release = oc_json("get", "clusterversion", "version")["status"]["desired"]["image"]
    proxy_image = run(
        ["oc", "adm", "release", "info", release, "--image-for=oauth-proxy"],
        capture=True,
    ).strip()
    run(
        [
            python,
            ROOT / "scripts/expose_dashboard.py",
            "--component",
            "mlflow",
            "--oauth-proxy-image",
            proxy_image,
        ],
        env=env,
    )
    if mlflow_exists:
        run(["oc", "-n", NS, "rollout", "restart", "deployment/mlflow"])
    run(["oc", "-n", NS, "rollout", "status", "deployment/mlflow", "--timeout=300s"])

    print("[7/8] Starting the review worker", flush=True)
    run(["oc", "apply", "-f", ROOT / "deploy/worker.yaml"])
    run(["oc", "-n", NS, "rollout", "restart", "deployment/worker"])
    run(["oc", "-n", NS, "rollout", "status", "deployment/worker", "--timeout=300s"])

    print("[8/8] Publishing the actual dashboard with OpenShift login", flush=True)
    run(
        [
            python,
            ROOT / "scripts/expose_dashboard.py",
            "--oauth-proxy-image",
            proxy_image,
        ],
        env=env,
    )
    run(
        [
            "oc",
            "-n",
            "openshell",
            "rollout",
            "status",
            "deployment/openshell-dashboard",
            "--timeout=300s",
        ]
    )
    app = oc_json("-n", NS, "get", "route", "red-compass")["spec"]["host"]
    dashboard = oc_json("-n", "openshell", "get", "route", "openshell-dashboard")[
        "spec"
    ]["host"]
    mlflow = oc_json("-n", NS, "get", "route", "mlflow")["spec"]["host"]
    print(
        f"\nDemo ready.\nCustomer portal: https://{app}\nOpenShell dashboard: https://{dashboard}\nMLflow traces: https://{mlflow}\nSeed requests are queued for real agent review; sign in as Alex or Sam to follow them.",
        flush=True,
    )


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, TimeoutError, KeyError) as exc:
        print("Launch stopped: " + str(exc), file=sys.stderr)
        sys.exit(1)
