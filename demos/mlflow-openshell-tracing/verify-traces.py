"""Verify that MLflow traces were recorded from the agent run.

Queries the MLflow tracking server for traces in the demo experiment and
prints a summary. Run outside the sandbox (on a machine with network access
to the MLflow route).

Usage:
    export MLFLOW_TRACKING_URI=<mlflow-route-url>
    export MLFLOW_TRACKING_TOKEN=$(oc whoami -t)
    export MLFLOW_TRACKING_INSECURE_TLS=true
    python verify-traces.py [--experiment openshell-tracing-demo] [--workspace default]
"""

import argparse
import json
import os
import sys

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify MLflow traces from OpenShell demo")
    parser.add_argument(
        "--experiment",
        default="openshell-tracing-demo",
        help="MLflow experiment name (default: openshell-tracing-demo)",
    )
    parser.add_argument(
        "--workspace",
        default=os.environ.get("MLFLOW_WORKSPACE", "default"),
        help="MLflow workspace / K8s namespace (default: 'default')",
    )
    args = parser.parse_args()

    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        print("MLFLOW_TRACKING_URI not set.")
        sys.exit(1)

    token = os.environ.get("MLFLOW_TRACKING_TOKEN")
    verify_tls = os.environ.get("MLFLOW_TRACKING_INSECURE_TLS", "").lower() != "true"

    headers = {"X-MLflow-Workspace": args.workspace}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Find experiment by name
    resp = requests.get(
        f"{tracking_uri}/api/2.0/mlflow/experiments/get-by-name",
        params={"experiment_name": args.experiment},
        headers=headers,
        verify=verify_tls,
    )
    if resp.status_code != 200:
        print(f"Experiment '{args.experiment}' not found: {resp.text}")
        sys.exit(1)

    experiment = resp.json()["experiment"]
    exp_id = experiment["experiment_id"]
    print(f"Experiment: {experiment['name']} (ID: {exp_id})")

    # Search for traces
    resp = requests.get(
        f"{tracking_uri}/api/2.0/mlflow/traces",
        params={"experiment_ids": exp_id, "max_results": 20},
        headers=headers,
        verify=verify_tls,
    )
    if resp.status_code != 200:
        print(f"Failed to fetch traces: {resp.text}")
        sys.exit(1)

    traces = resp.json().get("traces", [])
    if not traces:
        print("No traces found. The agent may not have run or tracing was not enabled.")
        sys.exit(1)

    print(f"\nFound {len(traces)} trace(s):\n")
    for trace in traces:
        print(f"  Request ID     : {trace.get('request_id', 'N/A')}")
        print(f"  Status         : {trace.get('status', 'N/A')}")
        print(f"  Timestamp      : {trace.get('timestamp_ms', 'N/A')}")
        print(f"  Duration       : {trace.get('execution_time_ms', 'N/A')}ms")
        print()

    print("Traces verified successfully.")


if __name__ == "__main__":
    main()
