"""
RedBank Orchestrator — A2A server with OpenAI-compatible /chat/completions shim.

Modelled after a2a_langgraph_crewai.langgraph_a2a_server:
  - Starlette app built by A2AStarletteApplication (handles /.well-known/agent-card.json + POST /)
  - Extra routes for /chat/completions and /health
  - Background re-discovery loop picks up new/removed agents every DISCOVERY_INTERVAL seconds
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from os import getenv
from typing import Any
from uuid import uuid4

import uvicorn
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import new_agent_text_message

from redbank_orchestrator.agent import build_graph_from_peers
from redbank_orchestrator.discovery import PeerAgent, discover_peers, peers_changed
from redbank_orchestrator.tracing import enable_tracing

load_dotenv()

_log_level = getattr(logging, getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
logging.basicConfig(level=_log_level)
logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

_DISCOVERY_INTERVAL = 15  # seconds between peer re-discovery checks


def _listen_port() -> int:
    return int(getenv("PORT", "8000"))


# ── Mutable state — graph + peers ────────────────────────────────────────────
#
# _graph and _peers are replaced atomically by _rebuild_graph().
# Python's GIL makes simple reference assignment thread-safe.

_graph = None
_peers: list[PeerAgent] = []
_graph_initialized = False


def _get_llm_config() -> dict[str, str | None]:
    """Read LLM env vars once."""
    base_url = getenv("LLM_BASE_URL")
    model_id = getenv("LLM_MODEL")
    if not base_url or not model_id:
        raise RuntimeError("LLM_BASE_URL and LLM_MODEL must be set (see .env.example).")
    if not base_url.endswith("/v1"):
        base_url = base_url.rstrip("/") + "/v1"
    return {"base_url": base_url, "model_id": model_id}


def _rebuild_graph(new_peers: list[PeerAgent]) -> None:
    """Rebuild the graph with a new set of peers (atomic swap)."""
    global _graph, _peers, _graph_initialized
    cfg = _get_llm_config()
    _graph = build_graph_from_peers(
        new_peers, model_id=cfg["model_id"], base_url=cfg["base_url"]
    )
    _peers = new_peers
    _graph_initialized = True


def _ensure_graph():
    """Lazy-init: build the graph on first request if not already built."""
    if not _graph_initialized:
        _init_graph()
    return _graph


async def _init_graph_async() -> None:
    """Run initial discovery and build the graph (async context)."""
    global _graph_initialized
    if _graph_initialized:
        return
    try:
        new_peers = await discover_peers()
        _rebuild_graph(new_peers)
        logger.info(
            "Initial discovery complete: %d peers: %s",
            len(_peers),
            [p.card.name for p in _peers],
        )
    except Exception:
        logger.exception("Initial graph build failed")


def _init_graph() -> None:
    """Synchronous wrapper for initial graph build (called from _ensure_graph)."""
    import concurrent.futures

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(asyncio.run, _init_graph_async()).result()
    else:
        asyncio.run(_init_graph_async())


# ── Background re-discovery ──────────────────────────────────────────────────


async def _rediscovery_loop() -> None:
    """Periodically re-discover peers and rebuild the graph if changed."""
    while True:
        await asyncio.sleep(_DISCOVERY_INTERVAL)
        try:
            new_peers = await discover_peers()
            if peers_changed(_peers, new_peers):
                old_names = {p.card.name for p in _peers}
                new_names = {p.card.name for p in new_peers}
                added = new_names - old_names
                removed = old_names - new_names
                logger.info(
                    "Peers changed — rebuilding graph. added=%s removed=%s total=%d",
                    added or "none",
                    removed or "none",
                    len(new_peers),
                )
                _rebuild_graph(new_peers)
            else:
                logger.debug("Re-discovery: no changes (%d peers)", len(new_peers))
        except Exception:
            logger.exception("Re-discovery failed — keeping current graph")


# ── Orchestrator invoke ──────────────────────────────────────────────────────


async def run_orchestrator(
    user_text: str,
    auth_token: str | None = None,
    context_id: str | None = None,
) -> str:
    """Shared invoke used by the A2A executor and /chat/completions.

    Args:
        user_text: The user's message text.
        auth_token: Optional Bearer token for downstream identity propagation.
        context_id: Optional A2A context ID, mapped to LangGraph's ``thread_id``
            so that successive calls within the same context share conversation
            history via the checkpointer.
    """
    graph = _ensure_graph()
    thread_id = context_id or uuid4().hex
    configurable: dict[str, Any] = {"thread_id": thread_id}
    if auth_token:
        configurable["auth_token"] = auth_token
    config: dict[str, Any] = {
        "recursion_limit": 10,
        "configurable": configurable,
    }
    out = await graph.ainvoke(
        {"messages": [HumanMessage(content=user_text)]}, config=config
    )
    for m in reversed(out.get("messages", [])):
        if isinstance(m, AIMessage) and m.content:
            return m.content
    return ""


# ── A2A executor ─────────────────────────────────────────────────────────────


class OrchestratorA2AExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user = context.get_user_input()
        if not user.strip():
            await event_queue.enqueue_event(
                new_agent_text_message("Error: empty user message.")
            )
            return

        auth_token: str | None = None
        if context.call_context and context.call_context.state:
            headers = context.call_context.state.get("headers", {})
            auth_token = headers.get("authorization")
            logger.debug("A2A auth: token=%s", "present" if auth_token else "absent")

        a2a_context_id = context.context_id

        try:
            reply = await run_orchestrator(
                user,
                auth_token=auth_token,
                context_id=a2a_context_id,
            )
            await event_queue.enqueue_event(new_agent_text_message(reply))
        except Exception as e:  # noqa: BLE001
            logger.exception("Orchestrator invoke failed")
            await event_queue.enqueue_event(
                new_agent_text_message(f"Orchestrator error: {e!s}")
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel not supported")


# ── Starlette route handlers ────────────────────────────────────────────────


def _make_completion_id() -> str:
    return f"chatcmpl-{uuid4().hex[:12]}"


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content")
            return c if isinstance(c, str) else str(c)
    return ""


async def _stream_sse(
    user_text: str,
    model_id: str,
    auth_token: str | None = None,
    context_id: str | None = None,
) -> AsyncIterator[str]:
    """Stream OpenAI chat.completion.chunk SSE events."""
    graph = _ensure_graph()
    completion_id = _make_completion_id()
    created = int(time.time())

    thread_id = context_id or uuid4().hex
    configurable: dict[str, Any] = {"thread_id": thread_id}
    if auth_token:
        configurable["auth_token"] = auth_token
    config: dict[str, Any] = {
        "recursion_limit": 10,
        "configurable": configurable,
    }

    tool_call_issued = False

    try:
        async for event in graph.astream_events(
            {"messages": [HumanMessage(content=user_text)]},
            config=config,
            version="v2",
        ):
            kind = event["event"]

            if kind == "on_chat_model_stream":
                if not tool_call_issued:
                    chunk = event["data"]["chunk"]
                    tc = getattr(chunk, "tool_call_chunks", None) or getattr(
                        chunk, "tool_calls", None
                    )
                    if tc:
                        continue
                    text = getattr(chunk, "content", None)
                    if text:
                        data = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model_id,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": text},
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(data)}\n\n"
                else:
                    chunk = event["data"]["chunk"]
                    text = getattr(chunk, "content", None)
                    if text:
                        data = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model_id,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"content": text},
                                    "finish_reason": None,
                                }
                            ],
                        }
                        yield f"data: {json.dumps(data)}\n\n"

            elif kind == "on_chat_model_end":
                message = event["data"]["output"]
                tool_calls = getattr(message, "tool_calls", None) or []
                if tool_calls:
                    tool_call_issued = True

            elif kind == "on_tool_end":
                pass

        yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model_id, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"

    except Exception:
        logger.exception("Stream failed")
        yield f"data: {json.dumps({'error': {'message': 'Internal server error', 'type': 'server_error'}})}\n\n"
        yield "data: [DONE]\n\n"


async def _chat_completions(request: Request) -> JSONResponse | StreamingResponse:
    try:
        body = await request.json()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}") from e

    messages = body.get("messages") or []
    if not isinstance(messages, list):
        raise HTTPException(status_code=400, detail="messages must be a list")
    stream = bool(body.get("stream", False))
    user_text = _last_user_text(messages)
    if not user_text.strip():
        raise HTTPException(status_code=400, detail="No user message in messages")

    model_id = body.get("model") or getenv("LLM_MODEL", "model")
    auth_token = request.headers.get("Authorization")

    if stream:
        return StreamingResponse(
            _stream_sse(user_text, model_id, auth_token),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        reply = await run_orchestrator(user_text, auth_token=auth_token)
    except Exception as e:  # noqa: BLE001
        logger.exception("Chat completions invoke failed")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return JSONResponse(
        {
            "id": _make_completion_id(),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": reply},
                    "finish_reason": "stop",
                }
            ],
        }
    )


async def _health(_request: Request) -> JSONResponse:
    try:
        _ensure_graph()
    except Exception:
        logger.exception("Health check failed — graph initialization error")
        return JSONResponse(
            {"status": "unhealthy", "agent_initialized": False}, status_code=503
        )
    return JSONResponse(
        {
            "status": "healthy",
            "agent_initialized": True,
            "peers": len(_peers),
        }
    )


# ── Build app ────────────────────────────────────────────────────────────────


def _build_agent_card() -> AgentCard:
    """Build the A2A agent card.

    Reads from the mutable ``_peers`` list so the card reflects the
    latest discovered peers after re-discovery updates them.
    """
    port = _listen_port()
    public_url = getenv("AGENT_PUBLIC_URL", f"http://localhost:{port}").rstrip("/")

    skills: list[AgentSkill] = []
    for peer in _peers:
        card = peer.card
        for skill in card.skills:
            skills.append(
                AgentSkill(
                    id=f"route-{peer.tool_name}-{skill.id}",
                    name=f"{card.name}: {skill.name}",
                    description=skill.description,
                    tags=skill.tags,
                    examples=skill.examples,
                )
            )

    if not skills:
        skills.append(
            AgentSkill(
                id="orchestrator",
                name="Multi-Agent Routing",
                description="Routes user queries to the appropriate specialist agent via A2A discovery.",
                tags=["orchestrator", "routing", "a2a"],
                examples=["How do I reset my password?", "What is my account balance?"],
            )
        )

    peer_names = (
        ", ".join(p.card.name for p in _peers) if _peers else "pending discovery"
    )

    return AgentCard(
        name="RedBank Orchestrator Agent",
        description=(
            f"Multi-agent orchestrator that classifies user intent and routes "
            f"queries to specialist agents via A2A. "
            f"Connected peers: {peer_names}."
        ),
        url=f"{public_url}/",
        version="0.1.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=skills,
        supports_authenticated_extended_card=False,
    )


@asynccontextmanager
async def _lifespan(app: Starlette):
    """Starlette lifespan: run initial discovery + launch re-discovery loop."""
    # Initial discovery — build graph before serving requests
    await _init_graph_async()
    logger.info("Starting re-discovery loop (interval=%ds)", _DISCOVERY_INTERVAL)
    task = asyncio.create_task(_rediscovery_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def build_app() -> Starlette:
    """Build the Starlette ASGI app with A2A + standard routes."""
    enable_tracing()

    agent_card = _build_agent_card()
    handler = DefaultRequestHandler(
        agent_executor=OrchestratorA2AExecutor(),
        task_store=InMemoryTaskStore(),
    )
    a2a_app = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=handler,
    )

    extra_routes = [
        Route("/health", _health, methods=["GET"]),
        Route("/chat/completions", _chat_completions, methods=["POST"]),
    ]
    return Starlette(
        routes=extra_routes + list(a2a_app.routes()),
        lifespan=_lifespan,
    )


app = build_app()


def main() -> None:
    port = _listen_port()
    logger.info(
        "RedBank Orchestrator listening on 0.0.0.0:%s; discovery_interval=%ds",
        port,
        _DISCOVERY_INTERVAL,
    )
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
