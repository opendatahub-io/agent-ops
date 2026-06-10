"""A2A server entry point for the RedBank Banking Operations Agent."""

from __future__ import annotations

import logging
import os

import mlflow
import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.server.apps.jsonrpc.jsonrpc_app import CallContextBuilder
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    HTTPAuthSecurityScheme,
    SecurityScheme,
)
from starlette.requests import Request

from .agent_executor import BankingAgentExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8001"))
AGENT_URL = os.getenv("AGENT_URL", f"http://localhost:{PORT}")


class BearerTokenContextBuilder(CallContextBuilder):
    """Extracts the incoming Bearer token from the HTTP Authorization header
    and stashes it into the ServerCallContext state so the AgentExecutor can
    propagate it downstream (e.g. to the MCP server)."""

    def build(self, request: Request) -> ServerCallContext:
        state: dict = {}
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            state["bearer_token"] = auth[7:]
        return ServerCallContext(state=state)


def _build_agent_card() -> AgentCard:
    skill = AgentSkill(
        id="banking_operations",
        name="Banking Operations",
        description=(
            "Admin-only write access to the RedBank customer database. "
            "Update account details and create new transactions. "
            "Read-only queries should be directed to the Knowledge Agent."
        ),
        tags=["banking", "admin", "write", "transactions", "account-updates"],
        examples=[
            "Update customer 5's phone number to 555-1234",
            "Create a $500 credit transaction for customer 2",
            "Change the address for alice.johnson@email.com",
        ],
    )

    return AgentCard(
        name="RedBank Banking Operations Agent",
        description=(
            "Admin-only banking operations agent with write access "
            "to the RedBank customer database via MCP. Handles account "
            "updates and new transaction creation. Read-only queries "
            "(transaction history, account summaries, document search) "
            "are handled by the Knowledge Agent."
        ),
        url=AGENT_URL,
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[skill],
        security=[{"bearer_auth": []}],
        security_schemes={
            "bearer_auth": SecurityScheme(
                root=HTTPAuthSecurityScheme(
                    scheme="Bearer",
                    bearer_format="JWT",
                    description="Keycloak JWT for RedBank realm",
                )
            )
        },
    )


def _configure_mlflow() -> None:
    """Configure MLflow tracking against OpenShift AI's in-cluster MLflow."""
    uri = os.getenv("MLFLOW_TRACKING_URI", "").strip()
    if not uri:
        logger.info("MLFLOW_TRACKING_URI not set; MLflow tracing disabled")
        return

    if os.getenv("MLFLOW_TRACKING_INSECURE_TLS", "").lower() in ("true", "1"):
        os.environ["MLFLOW_TRACKING_INSECURE_TLS"] = "true"

    mlflow.set_tracking_uri(uri)
    experiment = os.getenv("MLFLOW_EXPERIMENT_NAME", "banking-agent")
    mlflow.set_experiment(experiment)
    mlflow.langchain.autolog()
    auth = os.getenv("MLFLOW_TRACKING_AUTH", "default")
    insecure = os.getenv("MLFLOW_TRACKING_INSECURE_TLS", "false")
    logger.info(
        "MLflow: tracking_uri=%s experiment=%s auth=%s insecure_tls=%s",
        uri, experiment, auth, insecure,
    )


def main() -> None:
    _configure_mlflow()

    agent_card = _build_agent_card()
    logger.info("Agent card: %s @ %s", agent_card.name, agent_card.url)

    handler = DefaultRequestHandler(
        agent_executor=BankingAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    app_builder = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=handler,
        context_builder=BearerTokenContextBuilder(),
    )

    logger.info("Starting A2A server on %s:%d", HOST, PORT)
    uvicorn.run(app_builder.build(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
