"""RedBank Orchestrator Agent — discovers peers via A2A and routes to them dynamically.

Supports periodic re-discovery: the graph can be rebuilt with new peers
while preserving conversation state via a shared checkpointer.
"""

from __future__ import annotations

import logging
from os import getenv

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver

from redbank_orchestrator.discovery import PeerAgent
from redbank_orchestrator.tools import create_tools_from_peers

logger = logging.getLogger(__name__)

# Module-level checkpointer — survives graph rebuilds so active
# conversations (keyed by thread_id) are not lost on re-discovery.
_checkpointer = MemorySaver()


def _build_system_prompt(peers: list[PeerAgent]) -> str:
    """Build a routing system prompt dynamically from discovered agent cards."""
    if not peers:
        return (
            "You are the RedBank Orchestrator. No downstream agents are currently "
            "available. Inform the user that the system is starting up or "
            "misconfigured and suggest they try again shortly."
        )

    agent_descriptions: list[str] = []
    for i, peer in enumerate(peers, 1):
        card = peer.card
        lines = [f"{i}. **{card.name}** ({peer.tool_name})"]
        if card.description:
            lines.append(f"   {card.description}")
        if card.skills:
            for skill in card.skills:
                lines.append(f"   - {skill.name}: {skill.description}")
                if skill.tags:
                    lines.append(f"     Tags: {', '.join(skill.tags)}")
                if skill.examples:
                    examples = ", ".join(f'"{e}"' for e in skill.examples[:3])
                    lines.append(f"     Examples: {examples}")
        agent_descriptions.append("\n".join(lines))

    agents_block = "\n\n".join(agent_descriptions)

    return f"""\
You are the RedBank Orchestrator, a helpful banking assistant that routes user queries \
to the right specialist agent. You have access to the following downstream agents:

{agents_block}

ROUTING RULES:
- Analyse the user's intent and pick the most appropriate agent tool.
- Call exactly ONE tool per user query. Do not call multiple tools for the same question.
- After receiving a tool result, present the answer clearly to the user. Do not call the tool again.
- If a tool returns an access denied error, explain to the user that the operation requires appropriate privileges.
- If the user greets you or asks a general question you can answer yourself, respond directly.
- If the user's request is ambiguous or you need more information to pick the right agent, ask a clarifying question instead of guessing.
- This is a multi-turn conversation — the user may refer to previous messages. Use context from earlier turns when interpreting follow-up requests.
- Always be professional and concise in your responses."""


def build_graph_from_peers(
    peers: list[PeerAgent],
    *,
    model_id: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
):
    """Build a compiled LangGraph agent from a list of peers.

    Takes an already-discovered peer list so the caller (server.py)
    can rebuild the graph when peers change without losing the shared
    checkpointer.

    Returns:
        The compiled LangGraph agent.
    """
    if not api_key:
        api_key = getenv("OPENAI_API_KEY")
    if not base_url:
        base_url = getenv("LLM_BASE_URL")
    if not model_id:
        model_id = getenv("LLM_MODEL")

    is_local = any(host in base_url for host in ["localhost", "127.0.0.1"])
    if not is_local and not api_key:
        raise ValueError("OPENAI_API_KEY is required for non-local environments.")

    tools = create_tools_from_peers(peers)
    system_prompt = _build_system_prompt(peers)

    logger.info(
        "Orchestrator configured with %d tools: %s",
        len(tools),
        [t.name for t in tools],
    )

    chat = ChatOpenAI(
        model=model_id,
        temperature=0.01,
        api_key=api_key or "not-needed-for-local-development",
        base_url=base_url,
    )

    return create_agent(
        model=chat,
        tools=tools,
        system_prompt=system_prompt,
        checkpointer=_checkpointer,
    )
