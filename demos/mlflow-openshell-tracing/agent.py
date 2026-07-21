"""Demo agent: MLflow-traced inference via OpenShell sandbox.

Calls inference.local through the OpenAI SDK with MLflow auto-instrumentation
enabled. All LLM calls are captured as MLflow traces and sent to the tracking
server specified by MLFLOW_TRACKING_URI.

Usage (inside an OpenShell sandbox):
    python3 /sandbox/agent.py
"""

import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant."},
    {
        "role": "user",
        "content": (
            "Explain how container sandboxes improve the security of AI agent "
            "deployments in three sentences."
        ),
    },
]


def configure_tracing() -> bool:
    """Enable MLflow tracing if MLFLOW_TRACKING_URI is set."""
    tracking_uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        logger.info("MLFLOW_TRACKING_URI not set — tracing disabled")
        return False

    import mlflow
    import mlflow.openai

    mlflow.set_tracking_uri(tracking_uri)

    experiment_name = os.environ.get(
        "MLFLOW_EXPERIMENT_NAME", "openshell-tracing-demo"
    )
    mlflow.set_experiment(experiment_name)
    mlflow.config.enable_async_logging()
    mlflow.openai.autolog()

    logger.info("MLflow tracing enabled — %s (experiment: %s)", tracking_uri, experiment_name)
    return True


def run_agent() -> None:
    """Make a chat completion call via inference.local."""
    from openai import OpenAI

    client = OpenAI(api_key="unused", base_url="https://inference.local/v1")

    logger.info("Sending chat completion request via inference.local ...")
    response = client.chat.completions.create(
        model="router",
        messages=MESSAGES,
        temperature=0,
    )

    content = (response.choices[0].message.content or "").strip()
    print("\n--- Agent Response ---")
    print(content)
    print(f"\nModel: {response.model}")
    print(f"Tokens: {response.usage.prompt_tokens} prompt, {response.usage.completion_tokens} completion")


def main() -> None:
    tracing_ok = False
    try:
        tracing_ok = configure_tracing()
    except Exception as e:
        logger.warning("Failed to configure tracing: %s — continuing without it", e)

    try:
        run_agent()
    except Exception as e:
        logger.error("Agent failed: %s", e)
        sys.exit(1)

    if tracing_ok:
        logger.info("Trace sent to MLflow. Check the experiment UI to verify.")


if __name__ == "__main__":
    main()
