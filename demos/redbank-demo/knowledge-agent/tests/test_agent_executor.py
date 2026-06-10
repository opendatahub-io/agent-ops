"""Tests for the A2A agent executor and entry point."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_agent.agent_executor import (
    KnowledgeAgentExecutor,
    _extract_bearer_token,
    _extract_user_text,
)


def _make_request_context(text: str = "", bearer_token: str | None = None):
    """Build a mock RequestContext."""
    ctx = MagicMock()

    if text:
        part = MagicMock()
        part.text = text
        part_wrapper = MagicMock()
        part_wrapper.root = part
        ctx.message.parts = [part_wrapper]
    else:
        ctx.message.parts = []

    if bearer_token:
        ctx.call_context.state = {"bearer_token": bearer_token}
    else:
        ctx.call_context.state = {}

    return ctx


class TestExtractUserText:
    def test_extracts_text(self):
        ctx = _make_request_context("Hello")
        assert _extract_user_text(ctx) == "Hello"

    def test_empty_parts(self):
        ctx = _make_request_context("")
        assert _extract_user_text(ctx) == ""


class TestExtractBearerToken:
    def test_extracts_token(self):
        ctx = _make_request_context(bearer_token="my-jwt")
        assert _extract_bearer_token(ctx) == "my-jwt"

    def test_no_token(self):
        ctx = _make_request_context()
        assert _extract_bearer_token(ctx) is None


class TestKnowledgeAgentExecutor:
    @pytest.mark.asyncio
    @patch("knowledge_agent.agent_executor.create_agent_with_tools")
    async def test_execute_enqueues_response(self, mock_create):
        graph = AsyncMock()
        graph.ainvoke.return_value = {
            "messages": [MagicMock(content="Here is the answer.")]
        }
        mock_create.return_value = (graph, AsyncMock())

        executor = KnowledgeAgentExecutor()
        ctx = _make_request_context("What is my balance?", bearer_token="jwt")
        queue = AsyncMock()

        await executor.execute(ctx, queue)

        queue.enqueue_event.assert_called_once()
        msg = queue.enqueue_event.call_args[0][0]
        assert "answer" in msg.parts[0].root.text.lower()

    @pytest.mark.asyncio
    async def test_execute_empty_message(self):
        executor = KnowledgeAgentExecutor()
        ctx = _make_request_context("")
        queue = AsyncMock()

        await executor.execute(ctx, queue)

        queue.enqueue_event.assert_called_once()
        msg = queue.enqueue_event.call_args[0][0]
        assert "didn't receive" in msg.parts[0].root.text.lower()

    @pytest.mark.asyncio
    @patch("knowledge_agent.agent_executor.create_agent_with_tools")
    async def test_execute_handles_rate_limit(self, mock_create):
        from openai import RateLimitError

        mock_create.side_effect = RateLimitError(
            message="rate limit",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )

        executor = KnowledgeAgentExecutor()
        ctx = _make_request_context("Hello", bearer_token="jwt")
        queue = AsyncMock()

        await executor.execute(ctx, queue)

        queue.enqueue_event.assert_called_once()
        msg = queue.enqueue_event.call_args[0][0]
        assert "overloaded" in msg.parts[0].root.text.lower()
