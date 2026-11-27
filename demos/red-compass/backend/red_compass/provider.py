"""OpenShell provider profile derived from the configured inference endpoint."""

from .config import model_endpoint, settings


def model_provider_profile(cfg=None):
    cfg = cfg or settings()
    host, port = model_endpoint(cfg)
    return {
        "id": cfg.openshell_provider,
        "display_name": "Red Compass model endpoint",
        "description": "Anthropic Messages-compatible inference",
        "category": 2,
        "inference_capable": True,
        "credentials": [
            {
                "name": "api_key",
                "env_vars": ["ANTHROPIC_API_KEY"],
                "required": True,
                "auth_style": "header",
                "header_name": "x-api-key",
            }
        ],
        "discovery": {"credentials": ["api_key"]},
        "endpoints": [
            {
                "host": host,
                "port": port,
                "protocol": "rest",
                "access": 2,
                "enforcement": 1,
            }
        ],
        "binaries": [{"path": "/usr/bin/python3.12"}],
    }
