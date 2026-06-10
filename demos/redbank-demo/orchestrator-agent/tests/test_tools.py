import sys
import os
from unittest.mock import AsyncMock, patch

import pytest

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.redbank_orchestrator.tools import (
    AgentQueryInput,
    _get_auth_token,
    _get_context_id,
    _build_tool_description,
    create_tools_from_peers,
)
from src.redbank_orchestrator.discovery import PeerAgent, _slugify

from a2a.types import AgentCapabilities, AgentCard, AgentSkill


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_card(
    name: str = "Test Agent",
    description: str = "A test agent.",
    skills: list[AgentSkill] | None = None,
) -> AgentCard:
    """Create a minimal AgentCard for testing."""
    if skills is None:
        skills = [
            AgentSkill(
                id="test-skill",
                name="Test Skill",
                description="Does test things.",
                tags=["test"],
                examples=["Do a test"],
            )
        ]
    return AgentCard(
        name=name,
        description=description,
        url="http://test:8001/",
        version="0.1.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=skills,
    )


def _make_peer(
    url: str = "http://test:8001",
    name: str = "Test Agent",
    description: str = "A test agent.",
    skills: list[AgentSkill] | None = None,
) -> PeerAgent:
    card = _make_card(name=name, description=description, skills=skills)
    return PeerAgent(url=url, card=card)


# ── Schema tests ─────────────────────────────────────────────────────────────


def test_agent_query_input_schema():
    """Test that the AgentQueryInput schema is properly defined."""
    schema = AgentQueryInput(question="What is my account balance?")
    assert schema.question == "What is my account balance?"


def test_agent_query_input_has_description():
    """Test that schema fields have descriptions."""
    json_schema = AgentQueryInput.model_json_schema()
    assert "properties" in json_schema
    assert "question" in json_schema["properties"]
    assert "description" in json_schema["properties"]["question"]


# ── Auth token extraction tests ──────────────────────────────────────────────


def test_get_auth_token_from_config():
    """Test that auth token is extracted from RunnableConfig."""
    config = {"configurable": {"auth_token": "Bearer test-token-123"}}
    assert _get_auth_token(config) == "Bearer test-token-123"


def test_get_auth_token_missing_config():
    """Test that None is returned when config is None."""
    assert _get_auth_token(None) is None


def test_get_auth_token_missing_configurable():
    """Test that None is returned when configurable is missing."""
    assert _get_auth_token({}) is None


def test_get_auth_token_missing_auth_token():
    """Test that None is returned when auth_token is not in configurable."""
    assert _get_auth_token({"configurable": {}}) is None


# ── Context ID extraction tests ─────────────────────────────────────────────


def test_get_context_id_from_config():
    """Test that context_id (stored as thread_id) is extracted from RunnableConfig."""
    config = {"configurable": {"thread_id": "ctx-abc-123"}}
    assert _get_context_id(config) == "ctx-abc-123"


def test_get_context_id_missing_config():
    """Test that None is returned when config is None."""
    assert _get_context_id(None) is None


def test_get_context_id_missing_configurable():
    """Test that None is returned when configurable is missing."""
    assert _get_context_id({}) is None


def test_get_context_id_missing_thread_id():
    """Test that None is returned when thread_id is not in configurable."""
    assert _get_context_id({"configurable": {}}) is None


def test_get_context_id_with_auth_token_present():
    """Test that context_id is extracted even when auth_token is also present."""
    config = {"configurable": {"auth_token": "Bearer tok", "thread_id": "ctx-456"}}
    assert _get_context_id(config) == "ctx-456"
    assert _get_auth_token(config) == "Bearer tok"


# ── Discovery helper tests ──────────────────────────────────────────────────


def test_slugify_simple():
    assert _slugify("Knowledge Agent") == "ask_knowledge_agent"


def test_slugify_already_prefixed():
    assert _slugify("Ask Banking Agent") == "ask_banking_agent"


def test_slugify_special_chars():
    assert _slugify("My Agent (v2)") == "ask_my_agent_v2"


# ── Tool description builder tests ──────────────────────────────────────────


def test_build_tool_description_includes_name():
    peer = _make_peer(name="Knowledge Agent")
    desc = _build_tool_description(peer)
    assert "Knowledge Agent" in desc


def test_build_tool_description_includes_skills():
    peer = _make_peer(
        skills=[
            AgentSkill(
                id="rag",
                name="RAG Retrieval",
                description="Retrieves docs from vector store.",
                tags=["rag"],
                examples=["Find the password reset policy"],
            )
        ]
    )
    desc = _build_tool_description(peer)
    assert "RAG Retrieval" in desc
    assert "Retrieves docs from vector store" in desc
    assert "password reset policy" in desc


def test_build_tool_description_no_skills():
    peer = _make_peer(skills=[])
    desc = _build_tool_description(peer)
    assert "Test Agent" in desc
    assert "Capabilities" not in desc


# ── Dynamic tool creation tests ─────────────────────────────────────────────


def test_create_tools_from_peers_creates_correct_count():
    peers = [
        _make_peer(url="http://a:8001", name="Knowledge Agent"),
        _make_peer(url="http://b:8002", name="Banking Agent"),
    ]
    tools = create_tools_from_peers(peers)
    assert len(tools) == 2


def test_create_tools_from_peers_names():
    peers = [
        _make_peer(url="http://a:8001", name="Knowledge Agent"),
        _make_peer(url="http://b:8002", name="Banking Agent"),
    ]
    tools = create_tools_from_peers(peers)
    names = {t.name for t in tools}
    assert "ask_knowledge_agent" in names
    assert "ask_banking_agent" in names


def test_create_tools_from_peers_empty():
    tools = create_tools_from_peers([])
    assert tools == []


def test_created_tool_has_args_schema():
    peers = [_make_peer()]
    tools = create_tools_from_peers(peers)
    assert tools[0].args_schema == AgentQueryInput


@pytest.mark.asyncio
@patch("src.redbank_orchestrator.tools.send_a2a_text_message", new_callable=AsyncMock)
async def test_dynamic_tool_invokes_a2a(mock_send):
    """Test that a dynamically created tool calls A2A with the correct peer URL."""
    mock_send.return_value = "Your balance is $1,234.56"

    peer = _make_peer(url="http://knowledge:8080", name="Knowledge Agent")
    tools = create_tools_from_peers([peer])
    tool = tools[0]

    result = await tool.ainvoke({"question": "What is my balance?"})

    assert result == "Your balance is $1,234.56"
    mock_send.assert_called_once_with(
        "http://knowledge:8080",
        "What is my balance?",
        auth_token=None,
        context_id=None,
    )


@pytest.mark.asyncio
@patch("src.redbank_orchestrator.tools.send_a2a_text_message", new_callable=AsyncMock)
async def test_dynamic_tool_multiple_peers(mock_send):
    """Test that each tool routes to its own peer URL."""
    mock_send.side_effect = ["knowledge response", "banking response"]

    peers = [
        _make_peer(url="http://knowledge:8001", name="Knowledge Agent"),
        _make_peer(url="http://banking:8002", name="Banking Agent"),
    ]
    tools = create_tools_from_peers(peers)

    r1 = await tools[0].ainvoke({"question": "q1"})
    r2 = await tools[1].ainvoke({"question": "q2"})

    assert r1 == "knowledge response"
    assert r2 == "banking response"
    assert mock_send.call_args_list[0][0][0] == "http://knowledge:8001"
    assert mock_send.call_args_list[1][0][0] == "http://banking:8002"


# ── Context propagation through tools ────────────────────────────────────────


@pytest.mark.asyncio
@patch("src.redbank_orchestrator.tools.send_a2a_text_message", new_callable=AsyncMock)
async def test_dynamic_tool_propagates_context_id(mock_send):
    """Test that context_id (thread_id) from RunnableConfig is forwarded to A2A."""
    mock_send.return_value = "response with context"

    peer = _make_peer(url="http://knowledge:8080", name="Knowledge Agent")
    tools = create_tools_from_peers([peer])
    tool = tools[0]

    config = {"configurable": {"thread_id": "ctx-multi-turn-123"}}
    result = await tool.ainvoke({"question": "follow-up question"}, config=config)

    assert result == "response with context"
    mock_send.assert_called_once_with(
        "http://knowledge:8080",
        "follow-up question",
        auth_token=None,
        context_id="ctx-multi-turn-123",
    )


@pytest.mark.asyncio
@patch("src.redbank_orchestrator.tools.send_a2a_text_message", new_callable=AsyncMock)
async def test_dynamic_tool_propagates_both_auth_and_context(mock_send):
    """Test that both auth_token and context_id are forwarded when present."""
    mock_send.return_value = "authenticated contextual response"

    peer = _make_peer(url="http://banking:8080", name="Banking Agent")
    tools = create_tools_from_peers([peer])
    tool = tools[0]

    config = {
        "configurable": {
            "auth_token": "Bearer my-jwt-token",
            "thread_id": "ctx-session-456",
        }
    }
    result = await tool.ainvoke({"question": "transfer $100"}, config=config)

    assert result == "authenticated contextual response"
    mock_send.assert_called_once_with(
        "http://banking:8080",
        "transfer $100",
        auth_token="Bearer my-jwt-token",
        context_id="ctx-session-456",
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
