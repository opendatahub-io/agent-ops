"""Tests for Kubernetes-native agent discovery via AgentCard CRDs."""

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.redbank_orchestrator.k8s_discovery import (
    is_in_cluster,
    discover_from_k8s,
    _agentcard_to_peer,
    _build_a2a_card,
    _self_deployment_name,
)
from src.redbank_orchestrator.discovery import (
    discover_peers,
    PeerAgent,
)
from a2a.types import AgentCapabilities, AgentCard, AgentSkill


# ── Fixtures ────────────────────────────────────────────────────────────────


def _make_agentcard_cr(
    name: str = "knowledge-agent-deployment-card",
    agent_name: str = "Knowledge Agent",
    target_name: str = "knowledge-agent",
    protocol: str = "a2a",
    namespace: str = "redbank",
    skills: list[dict] | None = None,
) -> dict:
    """Build a minimal AgentCard CRD JSON object."""
    if skills is None:
        skills = [
            {
                "id": "rag",
                "name": "RAG Retrieval",
                "description": "Retrieves docs.",
                "tags": ["knowledge"],
                "examples": ["What is the password policy?"],
            }
        ]
    return {
        "apiVersion": "agent.kagenti.dev/v1alpha1",
        "kind": "AgentCard",
        "metadata": {
            "name": name,
            "namespace": namespace,
        },
        "spec": {
            "syncPeriod": "30s",
            "targetRef": {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "name": target_name,
            },
        },
        "status": {
            "protocol": protocol,
            "card": {
                "name": agent_name,
                "description": f"A {agent_name.lower()}.",
                "version": "1.0.0",
                "url": f"http://{target_name}.redbank.svc.cluster.local:8080/",
                "capabilities": {"streaming": False},
                "skills": skills,
                "defaultInputModes": ["text"],
                "defaultOutputModes": ["text"],
            },
            "targetRef": {
                "apiVersion": "apps/v1",
                "kind": "Deployment",
                "name": target_name,
            },
            "lastSyncTime": "2025-04-20T10:00:00Z",
        },
    }


def _make_k8s_list_response(*items: dict) -> dict:
    return {
        "apiVersion": "agent.kagenti.dev/v1alpha1",
        "kind": "AgentCardList",
        "metadata": {"resourceVersion": "12345"},
        "items": list(items),
    }


def _make_peer(name: str = "Test Agent", url: str = "http://test:8080") -> PeerAgent:
    card = AgentCard(
        name=name,
        description="A test agent.",
        url=url + "/",
        version="0.1.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="test",
                name="Test Skill",
                description="Does tests.",
                tags=["test"],
                examples=["Do a test"],
            )
        ],
    )
    return PeerAgent(url=url, card=card)


# ── is_in_cluster ───────────────────────────────────────────────────────────


def test_is_in_cluster_false(tmp_path):
    """Not in cluster when SA token doesn't exist."""
    with patch(
        "src.redbank_orchestrator.k8s_discovery._SA_TOKEN_PATH",
        tmp_path / "nonexistent",
    ):
        assert is_in_cluster() is False


def test_is_in_cluster_true(tmp_path):
    """In cluster when SA token file exists."""
    token_file = tmp_path / "token"
    token_file.write_text("fake-token")
    with patch(
        "src.redbank_orchestrator.k8s_discovery._SA_TOKEN_PATH",
        token_file,
    ):
        assert is_in_cluster() is True


# ── _self_deployment_name ───────────────────────────────────────────────────


def test_self_deployment_name_standard_pod():
    with patch.dict(os.environ, {"HOSTNAME": "redbank-orchestrator-5d8f9b7c4d-x9k2j"}):
        assert _self_deployment_name() == "redbank-orchestrator"


def test_self_deployment_name_short_hostname():
    with patch.dict(os.environ, {"HOSTNAME": "myapp"}):
        assert _self_deployment_name() == "myapp"


def test_self_deployment_name_no_hostname():
    with patch.dict(os.environ, {}, clear=True):
        assert _self_deployment_name() is None


# ── _build_a2a_card ─────────────────────────────────────────────────────────


def test_build_a2a_card_basic():
    card_data = {
        "name": "Knowledge Agent",
        "description": "Answers questions.",
        "version": "1.0.0",
        "capabilities": {"streaming": True},
        "skills": [
            {
                "id": "rag",
                "name": "RAG",
                "description": "Retrieves docs.",
                "tags": ["knowledge"],
                "examples": ["Find docs"],
            }
        ],
    }
    card = _build_a2a_card(card_data, "http://knowledge.ns.svc.cluster.local:8080")

    assert card.name == "Knowledge Agent"
    assert card.description == "Answers questions."
    assert card.url == "http://knowledge.ns.svc.cluster.local:8080/"
    assert card.version == "1.0.0"
    assert len(card.skills) == 1
    assert card.skills[0].id == "rag"
    assert card.skills[0].name == "RAG"
    assert card.skills[0].tags == ["knowledge"]


def test_build_a2a_card_no_skills():
    card_data = {"name": "Empty Agent", "description": "No skills."}
    card = _build_a2a_card(card_data, "http://empty.ns.svc.cluster.local:8080")

    assert card.name == "Empty Agent"
    assert card.skills == []


# ── _agentcard_to_peer ──────────────────────────────────────────────────────


def test_agentcard_to_peer_success():
    cr = _make_agentcard_cr()
    peer = _agentcard_to_peer(cr, "redbank", self_name="redbank-orchestrator")

    assert peer is not None
    assert peer.card.name == "Knowledge Agent"
    assert peer.url == "http://knowledge-agent.redbank.svc.cluster.local:8080"
    assert peer.tool_name == "ask_knowledge_agent"


def test_agentcard_to_peer_skips_no_url():
    cr = _make_agentcard_cr()
    cr["status"]["card"]["url"] = ""
    peer = _agentcard_to_peer(cr, "redbank", self_name=None)
    assert peer is None


def test_agentcard_to_peer_skips_self():
    cr = _make_agentcard_cr(
        target_name="redbank-orchestrator",
        agent_name="RedBank Orchestrator Agent",
    )
    peer = _agentcard_to_peer(cr, "redbank", self_name="redbank-orchestrator")
    assert peer is None


def test_agentcard_to_peer_skips_no_card_data():
    cr = _make_agentcard_cr()
    cr["status"]["card"] = None
    peer = _agentcard_to_peer(cr, "redbank", self_name=None)
    assert peer is None


def test_agentcard_to_peer_skips_non_a2a():
    cr = _make_agentcard_cr(protocol="mcp")
    peer = _agentcard_to_peer(cr, "redbank", self_name=None)
    assert peer is None


def test_agentcard_to_peer_empty_protocol_is_ok():
    """An empty protocol should be treated as potentially A2A (not filtered)."""
    cr = _make_agentcard_cr(protocol="")
    peer = _agentcard_to_peer(cr, "redbank", self_name=None)
    assert peer is not None


# ── discover_from_k8s (integration-style with mocked HTTP) ──────────────────


@pytest.mark.asyncio
async def test_discover_from_k8s_not_in_cluster(tmp_path):
    """Returns empty list when not running in-cluster."""
    with patch(
        "src.redbank_orchestrator.k8s_discovery._SA_TOKEN_PATH",
        tmp_path / "nonexistent",
    ):
        peers = await discover_from_k8s()
    assert peers == []


@pytest.mark.asyncio
async def test_discover_from_k8s_success(tmp_path):
    """Successfully discovers peers from K8s API."""
    # Set up fake SA mount
    token_file = tmp_path / "token"
    token_file.write_text("fake-sa-token")
    ca_file = tmp_path / "ca.crt"
    ca_file.write_text("fake-ca")  # won't actually be used
    ns_file = tmp_path / "namespace"
    ns_file.write_text("redbank")

    response_data = _make_k8s_list_response(
        _make_agentcard_cr(
            name="knowledge-card",
            agent_name="Knowledge Agent",
            target_name="knowledge-agent",
        ),
        _make_agentcard_cr(
            name="banking-card",
            agent_name="Banking Agent",
            target_name="banking-agent",
        ),
        # This one should be skipped (self)
        _make_agentcard_cr(
            name="orchestrator-card",
            agent_name="Orchestrator",
            target_name="redbank-orchestrator",
        ),
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = response_data
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.redbank_orchestrator.k8s_discovery._SA_TOKEN_PATH", token_file),
        patch("src.redbank_orchestrator.k8s_discovery._SA_CA_PATH", ca_file),
        patch("src.redbank_orchestrator.k8s_discovery._SA_NS_PATH", ns_file),
        patch(
            "src.redbank_orchestrator.k8s_discovery.httpx.AsyncClient",
            return_value=mock_client,
        ),
        patch(
            "src.redbank_orchestrator.k8s_discovery._build_ssl_context",
            return_value=False,  # skip real SSL context
        ),
        patch.dict(os.environ, {"AGENT_SELF_NAME": "redbank-orchestrator"}),
    ):
        peers = await discover_from_k8s()

    assert len(peers) == 2
    names = {p.card.name for p in peers}
    assert "Knowledge Agent" in names
    assert "Banking Agent" in names
    assert "Orchestrator" not in names


@pytest.mark.asyncio
async def test_discover_from_k8s_api_error(tmp_path):
    """Gracefully handles K8s API errors."""
    token_file = tmp_path / "token"
    token_file.write_text("fake-sa-token")
    ns_file = tmp_path / "namespace"
    ns_file.write_text("redbank")

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = "Forbidden"
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "403 Forbidden", request=MagicMock(), response=mock_response
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.redbank_orchestrator.k8s_discovery._SA_TOKEN_PATH", token_file),
        patch(
            "src.redbank_orchestrator.k8s_discovery._SA_CA_PATH", tmp_path / "ca.crt"
        ),
        patch("src.redbank_orchestrator.k8s_discovery._SA_NS_PATH", ns_file),
        patch(
            "src.redbank_orchestrator.k8s_discovery.httpx.AsyncClient",
            return_value=mock_client,
        ),
    ):
        peers = await discover_from_k8s()

    assert peers == []


# ── discover_peers ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discover_peers_returns_empty_outside_cluster():
    """Returns empty list when not running in-cluster."""
    with patch(
        "src.redbank_orchestrator.k8s_discovery.is_in_cluster",
        return_value=False,
    ):
        peers = await discover_peers()
    assert peers == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
