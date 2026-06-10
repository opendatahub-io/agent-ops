"""LangGraph ReAct agent wired to the RedBank MCP server via MultiServerMCPClient."""

from __future__ import annotations

import logging
import os

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

logger = logging.getLogger(__name__)

MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "http://redbank-mcp-server:8000/mcp")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

BANKING_TOOLS = {"update_account", "create_transaction", "get_customer"}

SYSTEM_PROMPT = """You are the RedBank Banking Operations Agent.

## ABSOLUTE RULES — VIOLATION OF ANY OF THESE IS A CRITICAL FAILURE

1. ONLY report data that a tool ACTUALLY returned. If you did not receive data from a tool \
   call, you MUST NOT include it in your response. Inventing, guessing, or assuming data is \
   strictly forbidden.
2. If a tool returns an error (e.g. "admin privileges", "not authorized", "Authentication \
   error"), tell the user they do not have permission and STOP. Do NOT retry. Do NOT claim \
   the operation succeeded. Do NOT invent a result.
3. If a tool returns an empty result ({{}}, [], or null), say "No data was found for …" and \
   STOP. Do NOT guess values. Do NOT make up a record.
4. NEVER include tool call syntax in your response. No square brackets like \
   [create_transaction(...)], no function signatures, no code-fenced tool calls. Respond \
   only in natural language.

## Role

You are admin-only. You perform write operations on the RedBank customer database. \
For read-only queries (account summaries, transaction history, document search), tell the \
user to use the Knowledge Agent.

## Tools

- get_customer(email=..., phone=...): resolve a customer by email OR phone before writing. \
  Exactly one argument must be provided.
- update_account(customer_id, phone?, address?, account_type?): update account details.
- create_transaction(customer_id, amount, description, transaction_type, merchant?, \
  transaction_date?): create a transaction. transaction_type must be "CREDIT" or "DEBIT".

## Response format

- Confirm write operations by showing the record the tool returned — nothing more.
- Include relevant identifiers (customer_id, transaction_id, etc.).
- Keep responses concise."""


def _create_llm() -> ChatOpenAI:
    """Build the ChatOpenAI instance from environment variables."""
    kwargs: dict = {
        "model": LLM_MODEL,
        "api_key": OPENAI_API_KEY,
        "temperature": 0,
    }
    if LLM_BASE_URL:
        kwargs["base_url"] = LLM_BASE_URL
    return ChatOpenAI(**kwargs)


def _patch_mcp_error_handling(tools: list[BaseTool]) -> list[BaseTool]:
    """Intercept MCP tool errors and return them as text instead of raising.

    StructuredTool is Pydantic — instance _arun overrides are silently dropped.
    Patching ``coroutine`` (which StructuredTool._arun awaits directly) is the
    correct intercept point.
    """
    for tool in tools:
        orig_coroutine = getattr(tool, "coroutine", None)
        if orig_coroutine is None:
            continue

        uses_artifact = getattr(tool, "response_format", "content") == "content_and_artifact"

        async def _guarded(*args, _orig=orig_coroutine, _artifact=uses_artifact, **kwargs):
            try:
                result = await _orig(*args, **kwargs)
            except Exception as e:
                error_msg = str(e)
                logger.warning("Tool error (returned to LLM): %s", error_msg)
                return (error_msg, error_msg) if _artifact else error_msg
            return result

        tool.coroutine = _guarded
    return tools


async def create_agent_with_tools(bearer_token: str | None = None):
    """Create a LangGraph ReAct agent connected to the MCP server.

    Args:
        bearer_token: JWT to forward to the MCP server for RLS scoping.

    Returns:
        A tuple of (compiled_graph, mcp_client) — caller must manage
        the client lifecycle via ``async with``.
    """
    headers: dict[str, str] = {}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"

    client = MultiServerMCPClient(
        {
            "customer_data": {
                "url": MCP_SERVER_URL,
                "transport": "http",
                "headers": headers,
            },
        }
    )

    all_tools = await client.get_tools()
    tools = [t for t in all_tools if t.name in BANKING_TOOLS]
    tools = _patch_mcp_error_handling(tools)
    if not tools:
        logger.warning("No tools loaded from MCP server at %s", MCP_SERVER_URL)
    logger.info(
        "Loaded %d/%d tools (write-scoped): %s",
        len(tools), len(all_tools), [t.name for t in tools],
    )

    model = _create_llm()

    graph = create_react_agent(model, tools, prompt=SYSTEM_PROMPT)

    return graph, client
