import pytest

from red_compass.config import Settings, model_endpoint
from red_compass.provider import model_provider_profile


def configuration(**overrides):
    values = dict(
        anthropic_api_key="private-test-key",
        anthropic_base_url="https://inference.example.org/messages",
        anthropic_model="demo-model",
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "url,host,port",
    [
        ("https://inference.example.org/messages", "inference.example.org", 443),
        ("http://inference.example.org:8080/api", "inference.example.org", 8080),
    ],
)
def test_provider_limits_egress_to_configured_endpoint(url, host, port):
    cfg = configuration(anthropic_base_url=url)
    profile = model_provider_profile(cfg)
    assert profile["endpoints"] == [
        dict(host=host, port=port, protocol="rest", access=2, enforcement=1)
    ]
    assert profile["credentials"][0]["env_vars"] == ["ANTHROPIC_API_KEY"]
    assert cfg.anthropic_api_key not in repr(profile)
    assert cfg.anthropic_api_key not in repr(cfg)


@pytest.mark.parametrize(
    "overrides",
    [
        dict(anthropic_api_key=""),
        dict(anthropic_model=""),
        dict(anthropic_model="your-model-id"),
        dict(anthropic_base_url=""),
        dict(anthropic_base_url="https://llm.example.com"),
        dict(anthropic_base_url="https://user:private-test-key@inference.example.org"),
        dict(anthropic_base_url="https://inference.example.org?token=private-test-key"),
        dict(anthropic_base_url="https://inference.example.org#private-test-key"),
        dict(anthropic_base_url="https://inference.example.org:invalid"),
        dict(anthropic_base_url="https://inference.example.org:0"),
        dict(anthropic_base_url="file:///tmp/model"),
    ],
)
def test_invalid_model_configuration_fails_without_exposing_credentials(overrides):
    with pytest.raises(ValueError) as error:
        model_endpoint(configuration(**overrides))
    assert "private-test-key" not in str(error.value)
    assert "ANTHROPIC_" in str(error.value)
