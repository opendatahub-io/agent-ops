"""Tests for the A2A server entry point and agent card."""

from __future__ import annotations

from unittest.mock import patch

from knowledge_agent.__main__ import _build_agent_card, _configure_mlflow


class TestAgentCard:
    def test_agent_card_structure(self):
        card = _build_agent_card()
        assert card.name == "RedBank Knowledge Agent"
        assert card.version == "1.0.0"
        assert len(card.skills) == 1

    def test_agent_card_skill_id(self):
        card = _build_agent_card()
        assert card.skills[0].id == "knowledge_search"

    def test_agent_card_skill_tags(self):
        card = _build_agent_card()
        tags = card.skills[0].tags
        assert "knowledge" in tags
        assert "rag" in tags
        assert "read-only" in tags

    def test_agent_card_security(self):
        card = _build_agent_card()
        assert "bearer_auth" in card.security_schemes


class TestConfigureMLflow:
    @patch.dict("os.environ", {"MLFLOW_TRACKING_URI": ""}, clear=False)
    def test_disabled_when_no_uri(self):
        _configure_mlflow()

    @patch("knowledge_agent.__main__.mlflow")
    @patch.dict("os.environ", {
        "MLFLOW_TRACKING_URI": "http://mlflow:5000",
        "MLFLOW_EXPERIMENT_NAME": "test-exp",
    }, clear=False)
    def test_enabled_with_uri(self, mock_mlflow):
        _configure_mlflow()
        mock_mlflow.set_tracking_uri.assert_called_once_with("http://mlflow:5000")
        mock_mlflow.set_experiment.assert_called_once_with("test-exp")
        mock_mlflow.langchain.autolog.assert_called_once()
