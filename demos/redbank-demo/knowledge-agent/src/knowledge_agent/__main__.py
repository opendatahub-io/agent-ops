"""A2A server entry point for the RedBank Knowledge Agent."""

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

from .agent_executor import KnowledgeAgentExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8002"))
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
        id="knowledge_search",
        name="Knowledge Search & Customer Data",
        description=(
            "Read-only access to the RedBank knowledge base and customer database. "
            "Search policies, FAQs, and how-to guides via semantic search. "
            "Look up customers, view transactions and account summaries."
        ),
        tags=["knowledge", "rag", "read-only", "customer-data", "policies"],
        examples=[
            "How do I reset my password?",
            "What is the policy on overdraft fees?",
            "What is my account balance?",
            "Show me the transactions for customer 5",
            "How do I open a savings account?",
        ],
    )

    return AgentCard(
        name="RedBank Knowledge Agent",
        description=(
            "Read-only knowledge and data retrieval agent for the RedBank "
            "customer database and document knowledge base. Routes queries "
            "between semantic document search (RAG) and customer data lookups, "
            "all scoped by the caller's JWT role."
        ),
        url=AGENT_URL,
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
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

    mlflow.set_tracking_uri(uri)
    experiment = os.getenv("MLFLOW_EXPERIMENT_NAME", "knowledge-agent")
    mlflow.set_experiment(experiment)
    mlflow.langchain.autolog()
    auth = os.getenv("MLFLOW_TRACKING_AUTH", "default")
    logger.info(
        "MLflow: tracking_uri=%s experiment=%s auth=%s",
        uri, experiment, auth,
    )


def main() -> None:
    _configure_mlflow()

    agent_card = _build_agent_card()
    logger.info("Agent card: %s @ %s", agent_card.name, agent_card.url)

    request_handler = DefaultRequestHandler(
        agent_executor=KnowledgeAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    app_builder = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
        context_builder=BearerTokenContextBuilder(),
    )

    logger.info("Starting A2A server on %s:%d", HOST, PORT)
    uvicorn.run(app_builder.build(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()
