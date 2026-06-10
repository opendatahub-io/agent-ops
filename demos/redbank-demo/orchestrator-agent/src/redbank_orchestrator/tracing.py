import logging
from os import getenv
from typing import Optional

from dotenv import load_dotenv

logger = logging.getLogger("tracing")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)


def enable_tracing() -> None:
    """Enable MLflow tracing if MLFLOW_TRACKING_URI is set.

    Behavior:
    1. If MLFLOW_TRACKING_URI is not set: tracing is skipped.
    2. If MLFLOW_TRACKING_URI is set: configure MLflow autologging.
       If MLflow is unreachable at runtime, traces will fail gracefully
       via MLflow's async logging.
    """
    load_dotenv()
    tracking_uri: Optional[str] = getenv("MLFLOW_TRACKING_URI")
    if not tracking_uri:
        logger.info("[Tracing] MLFLOW_TRACKING_URI not set. Tracing is disabled.")
        return

    logger.info("[Tracing] MLFLOW_TRACKING_URI is set: %s — attempting to configure tracing", tracking_uri)

    try:
        import mlflow
        import mlflow.langchain

        mlflow.set_tracking_uri(tracking_uri)

        experiment_name: str = getenv(
            "MLFLOW_EXPERIMENT_NAME", "default-agent-experiment"
        )
        mlflow.set_experiment(experiment_name)
        mlflow.config.enable_async_logging()

        mlflow.langchain.autolog()

        logger.info(
            "[Tracing Enabled] MLflow -> %s, Experiment: %s",
            tracking_uri,
            experiment_name,
        )
    except ModuleNotFoundError:
        logger.warning("[Tracing] MLflow not installed. Skipping tracing.")
    except Exception as e:
        logger.warning(
            "[Tracing] Failed to configure MLflow tracing at %s. "
            "Continuing without tracing. Error: %s",
            tracking_uri,
            e,
        )
