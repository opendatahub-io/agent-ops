#!/usr/bin/env python3
"""
Spin up two mock A2A agents for testing the orchestrator end-to-end.

  Agent B (Knowledge)  — port 8001 — returns canned read-only answers
  Agent C (Banking Ops) — port 8002 — returns canned write-op confirmations

Usage:
    # Terminal 1: start the mock agents
    cd orchestrator-agent
    uv run python examples/mock_agents.py

    # Terminal 2: start the orchestrator
    make run-app

    # Terminal 3: test it
    curl -s http://localhost:8000/chat/completions \\
      -H 'Content-Type: application/json' \\
      -d '{"messages":[{"role":"user","content":"What is my account balance?"}]}' | python -m json.tool

    curl -s http://localhost:8000/chat/completions \\
      -H 'Content-Type: application/json' \\
      -d '{"messages":[{"role":"user","content":"Transfer 500 to account 12345"}]}' | python -m json.tool

    # Or test A2A directly:
    curl -s http://localhost:8001/.well-known/agent-card.json | python -m json.tool
    curl -s http://localhost:8002/.well-known/agent-card.json | python -m json.tool
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import uvicorn
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import new_agent_text_message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Canned responses ─────────────────────────────────────────────────────────

KNOWLEDGE_RESPONSES = {
    "balance": "Your current account balance is $12,345.67 as of today.",
    "transaction": (
        "Here are your recent transactions:\n"
        "  1. Apr 15 - Grocery Store - $45.23\n"
        "  2. Apr 14 - Gas Station - $38.50\n"
        "  3. Apr 12 - Online Shopping - $129.99"
    ),
    "password": (
        "To reset your password:\n"
        "  1. Go to redbank.com/reset\n"
        "  2. Enter your email address\n"
        "  3. Click the link in the email you receive\n"
        "  4. Enter your new password"
    ),
    "default": "Based on our knowledge base, I can help you with account inquiries, transaction history, and bank policies. Could you be more specific about what you'd like to know?",
}

BANKING_RESPONSES = {
    "transfer": "Transfer initiated successfully. $500.00 has been sent to account #12345. Transaction ID: TXN-2026-04-17-001. Allow 1-2 business days for processing.",
    "address": "Your address has been updated to 123 Main St, Springfield, IL 62701. The change will be reflected on your next statement.",
    "default": "Banking operation received. Please note this is a mock agent for testing. In production, this would execute the requested write operation after verifying admin privileges.",
}


def _match_response(text: str, responses: dict[str, str]) -> str:
    lower = text.lower()
    for keyword, response in responses.items():
        if keyword != "default" and keyword in lower:
            return response
    return responses["default"]


# ── Mock executors ───────────────────────────────────────────────────────────


class KnowledgeAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user = context.get_user_input()
        logger.info("[Knowledge Agent] Received: %s", user[:100])
        reply = _match_response(user, KNOWLEDGE_RESPONSES)
        await event_queue.enqueue_event(new_agent_text_message(reply))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


class BankingAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user = context.get_user_input()
        logger.info("[Banking Agent] Received: %s", user[:100])
        reply = _match_response(user, BANKING_RESPONSES)
        await event_queue.enqueue_event(new_agent_text_message(reply))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError


# ── Build apps ───────────────────────────────────────────────────────────────


def _build_mock_app(
    name: str,
    description: str,
    port: int,
    executor: AgentExecutor,
    skill_id: str,
    skill_name: str,
    skill_desc: str,
    examples: list[str],
):
    agent_card = AgentCard(
        name=name,
        description=description,
        url=f"http://localhost:{port}/",
        version="0.1.0-mock",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id=skill_id,
                name=skill_name,
                description=skill_desc,
                tags=["mock", "test"],
                examples=examples,
            )
        ],
        supports_authenticated_extended_card=False,
    )
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
    )
    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=handler,
    )
    from starlette.applications import Starlette

    return Starlette(routes=list(a2a_app.routes()))


async def main():
    knowledge_app = _build_mock_app(
        name="Mock Knowledge Agent",
        description="Mock agent for testing — returns canned knowledge responses.",
        port=8001,
        executor=KnowledgeAgentExecutor(),
        skill_id="knowledge",
        skill_name="Knowledge Retrieval",
        skill_desc="Returns canned responses for balance, transactions, and password reset.",
        examples=["What is my account balance?", "Show me my recent transactions."],
    )

    banking_app = _build_mock_app(
        name="Mock Banking Operations Agent",
        description="Mock agent for testing — returns canned banking operation confirmations.",
        port=8002,
        executor=BankingAgentExecutor(),
        skill_id="banking-ops",
        skill_name="Banking Operations",
        skill_desc="Returns canned confirmations for transfers and address updates.",
        examples=["Transfer $500 to account 12345.", "Update my address."],
    )

    config_b = uvicorn.Config(
        knowledge_app, host="0.0.0.0", port=8001, log_level="info"
    )
    config_c = uvicorn.Config(banking_app, host="0.0.0.0", port=8002, log_level="info")

    server_b = uvicorn.Server(config_b)
    server_c = uvicorn.Server(config_c)

    logger.info("Starting mock agents:")
    logger.info("  Knowledge Agent (B) -> http://localhost:8001")
    logger.info("  Banking Agent  (C) -> http://localhost:8002")
    logger.info("")
    logger.info("Test agent cards:")
    logger.info("  curl http://localhost:8001/.well-known/agent-card.json")
    logger.info("  curl http://localhost:8002/.well-known/agent-card.json")
    logger.info("")
    logger.info("Press Ctrl+C to stop.")

    # Run both servers concurrently
    await asyncio.gather(server_b.serve(), server_c.serve())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Mock agents stopped.")
