"""LangGraph ReAct agent wired to the RedBank MCP server — read-only with RAG."""

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

ALLOWED_TOOLS = frozenset({
    "get_customer",
    "get_customer_transactions",
    "get_account_summary",
    "search_knowledge",
})

SYSTEM_PROMPT = """You are the RedBank Knowledge Agent — an assistant that answers questions \
about RedBank policies and procedures, and retrieves customer account data on behalf of the \
current user. You are strictly read-only and cannot modify any data.

Tools available:
- search_knowledge(query, k=5): search the RedBank knowledge base for documents matching a \
  natural language query. Use this for policy questions, how-to guides, FAQs, and general \
  information about RedBank products and services.
- get_customer(email=..., phone=...): look up a customer by email OR phone. Exactly one of \
  the two arguments must be provided.
- get_account_summary(customer_id): account summary with latest balance.
- get_customer_transactions(customer_id, start_date?, end_date?): transaction history.

Routing guidance:
- "how do I...", "what is the policy on...", "explain..." → search_knowledge
- "look up customer...", "what is my balance...", "show transactions..." → customer data tools
- You may combine both in a single turn if the user's question spans both domains.

Behaviour:
- If a tool returns an empty result (empty dict {{}}, empty list [], or null), respond with \
  a clear statement like "I don't have information about that topic." and stop. Do NOT retry \
  with different queries and do NOT guess or invent an answer.
- If search_knowledge returns documents that do NOT actually answer the user's question, say \
  "I don't have information about that topic." Do NOT extrapolate, summarize unrelated content, \
  or construct an answer from general knowledge. Only answer if the returned documents directly \
  and specifically address what the user asked.
- If a tool returns an error message such as "admin privileges", "not authorized", or \
  "Authentication error", tell the user they do not have permission. Do NOT retry the tool.
- NEVER fabricate, infer, or guess data. Your answers must come ONLY from tool results. \
  If the tool results do not contain the answer, say so — do not fill in gaps from your \
  own knowledge.
- You must NEVER write out function/tool calls as text in your response.
- Format data cleanly and include relevant identifiers (customer_id, transaction_id, etc.)."""


def create_llm() -> ChatOpenAI:
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
    """Intercept MCP tool errors and return them as text to the LLM.

    Patching ``coroutine`` (which StructuredTool._arun awaits directly) is the
    correct intercept point — instance _arun overrides are silently dropped.
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


def _filter_allowed_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Keep only tools in the allow-list — future write tools won't leak."""
    allowed = [t for t in tools if t.name in ALLOWED_TOOLS]
    rejected = [t.name for t in tools if t.name not in ALLOWED_TOOLS]
    if rejected:
        logger.info("Filtered out tools not in allow-list: %s", rejected)
    return allowed


async def create_agent_with_tools(bearer_token: str | None = None):
    """Create a LangGraph ReAct agent connected to the MCP server.

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

    tools = await client.get_tools()
    tools = _filter_allowed_tools(tools)
    tools = _patch_mcp_error_handling(tools)
    if not tools:
        logger.warning("No tools loaded from MCP server at %s", MCP_SERVER_URL)

    model = create_llm()

    graph = create_react_agent(model, tools, prompt=SYSTEM_PROMPT)

    return graph, client
