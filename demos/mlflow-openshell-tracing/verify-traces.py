"""Verify that MLflow traces were recorded from the agent run.

Queries the MLflow tracking server for traces in the demo experiment and
prints a summary. Intended to run outside the sandbox (on the cluster or
a machine with network access to the MLflow route).

Usage:
    export MLFLOW_TRACKING_URI=<mlflow-route-or-service-url>
    python verify-traces.py [--experiment openshell-tracing-demo]
"""

import argparse
import sys

import mlflow


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify MLflow traces from OpenShell demo")
    parser.add_argument(
        "--experiment",
        default="openshell-tracing-demo",
        help="MLflow experiment name (default: openshell-tracing-demo)",
    )
    args = parser.parse_args()

    experiment = mlflow.get_experiment_by_name(args.experiment)
    if experiment is None:
        print(f"Experiment '{args.experiment}' not found.")
        print("Has the agent been run yet? Check MLFLOW_TRACKING_URI is correct.")
        sys.exit(1)

    print(f"Experiment: {experiment.name} (ID: {experiment.experiment_id})")

    traces = mlflow.search_traces(experiment_ids=[experiment.experiment_id])

    if traces.empty:
        print("No traces found. The agent may not have run or tracing was not enabled.")
        sys.exit(1)

    print(f"\nFound {len(traces)} trace(s):\n")
    for _, trace in traces.iterrows():
        print(f"  Request ID : {trace.get('request_id', 'N/A')}")
        print(f"  Status     : {trace.get('status', 'N/A')}")
        print(f"  Timestamp  : {trace.get('timestamp_ms', 'N/A')}")
        print(f"  Duration   : {trace.get('execution_time_ms', 'N/A')}ms")
        print()

    print("Traces verified successfully.")


if __name__ == "__main__":
    main()
