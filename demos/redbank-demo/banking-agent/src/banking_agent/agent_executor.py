"""A2A AgentExecutor that bridges incoming A2A requests to the LangGraph agent."""

from __future__ import annotations

import logging

from openai import RateLimitError
from typing_extensions import override

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import Part, TextPart

from .agent import create_agent_with_tools

logger = logging.getLogger(__name__)


def _extract_bearer_token(context: RequestContext) -> str | None:
    """Extract the Bearer token that the CallContextBuilder stashed in state."""
    try:
        call_ctx = context.call_context
        if call_ctx and call_ctx.state:
            return call_ctx.state.get("bearer_token")
    except Exception:
        pass
    return None


class BankingAgentExecutor(AgentExecutor):
    """Bridges A2A protocol to the LangGraph banking agent."""

    @override
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)

        user_text = context.get_user_input()
        if not user_text or not user_text.strip():
            await updater.update_status(
                "input-required",
                message=updater.new_agent_message(
                    parts=[Part(root=TextPart(text="I didn't receive a message. Please try again."))]
                ),
            )
            return

        bearer_token = _extract_bearer_token(context)
        logger.info(
            "Processing request (token=%s, task=%s, context=%s): %.80s",
            "present" if bearer_token else "absent",
            context.task_id,
            context.context_id,
            user_text,
        )

        try:
            await updater.start_work()

            graph, client = await create_agent_with_tools(bearer_token)
            result = await graph.ainvoke(
                {"messages": [{"role": "user", "content": user_text}]}
            )
            response_text = result["messages"][-1].content

            await updater.add_artifact(
                parts=[Part(root=TextPart(text=response_text))]
            )
            await updater.complete()

        except RateLimitError as e:
            logger.warning("LLM rate limit hit: %s", e)
            await updater.failed(
                message=updater.new_agent_message(
                    parts=[Part(root=TextPart(
                        text="The service is temporarily overloaded. "
                             "Please wait a moment and try again."
                    ))]
                ),
            )

        except Exception:
            logger.exception("Agent execution failed")
            await updater.failed(
                message=updater.new_agent_message(
                    parts=[Part(root=TextPart(
                        text="An error occurred while processing your request. "
                             "Please try again or contact support."
                    ))]
                ),
            )

    @override
    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        updater = TaskUpdater(event_queue, context.task_id, context.context_id)
        await updater.cancel(
            message=updater.new_agent_message(
                parts=[Part(root=TextPart(text="Task cancelled."))]
            ),
        )
