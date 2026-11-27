"""Expose operator viewers through OpenShift OAuth without logging secrets."""

import argparse
import base64
import json
import re
import secrets
import subprocess
from pathlib import Path

import yaml

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--component", choices=["dashboard", "mlflow"], default="dashboard")
parser.add_argument(
    "--host",
    help="Viewer hostname; default is derived from the current cluster ingress domain",
)
parser.add_argument(
    "--oauth-proxy-image",
    help="Optional OAuth proxy image matching the target cluster release",
)
args = parser.parse_args()
namespace = "red-compass" if args.component == "mlflow" else "openshell"
secret_name = (
    "mlflow-oauth" if args.component == "mlflow" else "openshell-dashboard-oauth"
)
host = args.host
if not host:
    domain = subprocess.check_output(
        [
            "oc",
            "get",
            "ingress.config.openshift.io",
            "cluster",
            "-o",
            "jsonpath={.spec.domain}",
        ],
        text=True,
    ).strip()
    host = (
        "mlflow-red-compass."
        if args.component == "mlflow"
        else "openshell-dashboard-openshell."
    ) + domain
if (
    not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host)
    or "." not in host
):
    parser.error("host must be a DNS hostname without a scheme, path, or port")
items = list(yaml.safe_load_all((root / "deploy/dashboard.yaml").read_text()))
if args.component == "mlflow":
    from mlflow_resources import resources as mlflow_resources

    if not args.oauth_proxy_image:
        parser.error(
            "--oauth-proxy-image from the cluster release is required for MLflow"
        )
    items = mlflow_resources(items, host, args.oauth_proxy_image)
resources = {"apiVersion": "v1", "kind": "List", "items": items}
for item in items:
    if item["kind"] == "Route":
        item["spec"]["host"] = host
    elif item["kind"] == "Deployment":
        proxy = next(
            c
            for c in item["spec"]["template"]["spec"]["containers"]
            if c["name"] == "oauth-proxy"
        )
        proxy["args"] = [
            "--redirect-url=https://" + host + "/oauth/callback"
            if arg.startswith("--redirect-url=")
            else arg
            for arg in proxy["args"]
        ]
        if args.oauth_proxy_image:
            proxy["image"] = args.oauth_proxy_image
check = subprocess.run(
    [
        "oc",
        "-n",
        namespace,
        "get",
        "secret",
        secret_name,
        "--ignore-not-found",
        "-o",
        "name",
    ],
    check=False,
    capture_output=True,
    text=True,
)
if check.returncode:
    raise RuntimeError("Could not read viewer OAuth cookie Secret")
if not check.stdout.strip():
    resource = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": secret_name, "namespace": namespace},
        "type": "Opaque",
        "stringData": {
            "cookie-secret": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        },
    }
    result = subprocess.run(
        ["oc", "create", "-f", "-"],
        input=json.dumps(resource),
        text=True,
        check=False,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeError("Could not create viewer OAuth cookie Secret")
    print("Viewer OAuth cookie Secret created")
else:
    print("Existing viewer OAuth cookie Secret retained")
subprocess.run(
    ["oc", "apply", "-f", "-"], input=json.dumps(resources), text=True, check=True
)
print(
    ("MLflow: https://" if args.component == "mlflow" else "Dashboard: https://") + host
)
