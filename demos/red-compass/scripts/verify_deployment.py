"""Verify routed UIs, initial seed decisions, and customer isolation without creating requests."""

import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid
from urllib.parse import urlparse

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def route(namespace, name):
    host = subprocess.check_output(
        ["oc", "-n", namespace, "get", "route", name, "-o", "jsonpath={.spec.host}"],
        text=True,
    ).strip()
    return "https://" + host


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait-seconds", type=int, default=1200)
    parser.add_argument(
        "--routes-only",
        action="store_true",
        help="Check HTTPS and operator login protection without waiting for seed reviews",
    )
    args = parser.parse_args()
    app = route("red-compass", "red-compass")
    dashboard = route("openshell", "openshell-dashboard")
    mlflow = route("red-compass", "mlflow")
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.get(app)
        response.raise_for_status()
        print("Customer portal: HTTPS reachable", flush=True)
        response = client.get(dashboard + "/api/v1/gateway/compatibility")
        if (
            response.status_code != 302
            or not urlparse(response.headers.get("location", "")).hostname
        ):
            raise RuntimeError(
                "Dashboard management API did not redirect to OpenShift login"
            )
        print(
            "Dashboard: HTTPS reachable; management API requires OpenShift login",
            flush=True,
        )

        response = client.get(mlflow + "/api/2.0/mlflow/experiments/search")
        if (
            response.status_code != 302
            or not urlparse(response.headers.get("location", "")).hostname
        ):
            raise RuntimeError(
                "MLflow management API did not redirect to OpenShift login"
            )
        print(
            "MLflow: HTTPS reachable; management API requires OpenShift login",
            flush=True,
        )

    if args.routes_only:
        return

    corpus = dotenv_values(ROOT / ".env").get("POLICY_VERSION") or "demo-2026-01"
    cases = json.loads((ROOT / "backend/red_compass/seed_data.json").read_text())
    expected = {
        "lantern": "approved",
        "stair": "denied",
        "pavilion": "approved",
        "habitat": "denied",
        "missing-evidence": "denied",
        "emergency-drain": "approved",
    }
    clients = {
        owner: httpx.Client(base_url=app, timeout=30) for owner in ["alex", "sam"]
    }
    try:
        for owner, client in clients.items():
            client.post("/api/session", json={"customer": owner}).raise_for_status()
        deadline = time.monotonic() + args.wait_seconds
        previous = None
        while time.monotonic() < deadline:
            states = {}
            for case in cases:
                key = f"red-compass:seed-v1:{corpus}:{case['key']}"
                ticket_id = str(uuid.uuid5(uuid.NAMESPACE_URL, key))
                response = clients[case["owner"]].get("/api/requests/" + ticket_id)
                response.raise_for_status()
                ticket = response.json()
                states[case["key"]] = (
                    ticket["status"]
                    if ticket["status"] != "pending"
                    else ticket["progress"]
                )
                if ticket["progress"] == "interrupted":
                    raise RuntimeError("Seed review interrupted: " + case["key"])
                if ticket["status"] in ["approved", "denied"]:
                    if ticket["status"] != expected[case["key"]]:
                        raise RuntimeError(
                            f"Unexpected decision for {case['key']}: {ticket['status']}"
                        )
                    other = "sam" if case["owner"] == "alex" else "alex"
                    if (
                        clients[other].get("/api/requests/" + ticket_id).status_code
                        != 404
                    ):
                        raise RuntimeError("Cross-customer request isolation failed")
            if states != previous:
                print(json.dumps(states), flush=True)
                previous = states
            if all(states.get(case) == status for case, status in expected.items()):
                print(
                    "PASS: all six real seed reviews completed with expected outcomes; customer isolation passed.",
                    flush=True,
                )
                return
            time.sleep(10)
        raise TimeoutError("Seed reviews did not finish in the verification window")
    finally:
        for client in clients.values():
            client.close()


if __name__ == "__main__":
    main()
