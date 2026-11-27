from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    database_url: str = "sqlite:///./red-compass.db"
    session_secret: str = ""
    agent_token_signing_secret: str = ""
    anthropic_api_key: str = Field(default="", repr=False)
    anthropic_base_url: str = ""
    anthropic_model: str = ""
    anthropic_thinking_mode: Literal["default", "disabled"] = "default"
    api_base_url: str = "http://127.0.0.1:8000"
    mlflow_tracking_uri: str = ""
    mlflow_experiment_name: str = "red-compass-reviews"
    policy_version: str = "demo-2026-01"
    embedding_model_id: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_device: str = "cpu"
    policy_dir: Path = ROOT / "policies"
    agent_max_turns: int = Field(default=30, ge=1, le=30)
    agent_timeout_seconds: int = 300
    agent_max_tools_per_turn: int = Field(default=8, ge=1)
    max_review_retries: int = 1
    sandbox_retention_seconds: int = 600
    openshell_workspace: str = "default"
    openshell_workload_template: str = "red-compass-reviewer"
    openshell_provider: str = "red-compass-model"
    worker_mode: str = "openshell"


@lru_cache
def settings():
    return Settings()


def model_endpoint(cfg=None, *, require_key=True):
    """Validate explicit model configuration without exposing credentials in errors."""
    cfg = cfg or settings()
    if require_key and not cfg.anthropic_api_key.strip():
        raise ValueError("Set ANTHROPIC_API_KEY before connecting to the model")
    if not cfg.anthropic_model.strip() or cfg.anthropic_model == "your-model-id":
        raise ValueError("Set ANTHROPIC_MODEL to the model ID served by your endpoint")
    try:
        url = urlsplit(cfg.anthropic_base_url)
        port = url.port
        valid = (
            url.scheme in ("http", "https")
            and url.hostname
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and url.hostname != "llm.example.com"
            and port != 0
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "Set ANTHROPIC_BASE_URL to an HTTP(S) endpoint URL without credentials, query parameters, or a fragment"
        )
    return url.hostname, port or (443 if url.scheme == "https" else 80)
