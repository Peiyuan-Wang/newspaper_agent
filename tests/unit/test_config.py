from pathlib import Path

from config.settings import load_provider_settings
from newspaper_agent.model.settings import ModelConfigurationError
import pytest
import yaml


def test_model_config_has_no_secret_value():
    settings = load_provider_settings(Path(__file__).parents[2] / "model_config.yaml")
    assert settings.api_key_env == "DEEPSEEK_API_KEY"
    assert settings.base_url == "https://api.deepseek.com/v1"
    assert settings.model == "deepseek-chat"


def test_openai_provider_is_available():
    settings = load_provider_settings(Path(__file__).parents[2] / "model_config.yaml", provider_name="openai")
    assert settings.api_key_env == "OPENAI_API_KEY"
    assert settings.base_url == "https://api.openai.com/v1"


@pytest.mark.parametrize("field,value", [
    ("base_url", "ftp://example.com"),
    ("base_url", "https://example.com?key=secret"),
    ("base_url", "https://user:secret@example.com"),
    ("base_url", "https://example.com:bad"),
    ("api_key_env", "invalid name"),
    ("model", ""),
    ("timeout", 0),
    ("max_attempts", 0),
    ("max_tokens", True),
    ("temperature", float("nan")),
    ("temperature", 3),
    ("initial_retry_delay", -1),
    ("api_key", "secret"),
    ("protocol", "unsupported"),
    ("wire_api", "unsupported"),
    ("wire_api", None),
    ("wire_api", ["responses"]),
    ("wire_api", True),
    ("temperature", True),
    ("max_tokens", 0),
    ("timeout", None),
])
def test_invalid_provider_config_is_rejected_without_exposing_values(tmp_path, field, value):
    values = dict(base_url="https://example.com/v1", model="test", api_key_env="TEST_KEY")
    values[field] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(dict(default_provider="test", providers={"test": values})), encoding="utf-8")
    with pytest.raises(ModelConfigurationError) as error:
        load_provider_settings(path)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("text", ["", "[]", "providers: []", "providers: {}", "providers: {test: 1}\ndefault_provider: test", "[bad yaml"])
def test_invalid_yaml_or_schema(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ModelConfigurationError):
        load_provider_settings(path)


def test_missing_file_and_unknown_provider(tmp_path):
    with pytest.raises(ModelConfigurationError, match="无法读取"):
        load_provider_settings(tmp_path / "missing.yaml")
    with pytest.raises(ModelConfigurationError, match="provider 未定义"):
        load_provider_settings(Path(__file__).parents[2] / "model_config.yaml", "unknown")


@pytest.mark.parametrize("wire_api", ["chat", "responses"])
def test_wire_api_and_optional_parameters(tmp_path, wire_api):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "default_provider": "renamed",
        "providers": {"renamed": {
            "base_url": "https://relay.example/v1", "model": "test",
            "api_key_env": "TEST_KEY", "wire_api": wire_api,
            "temperature": None, "max_tokens": None,
        }},
    }), encoding="utf-8")
    settings = load_provider_settings(path)
    assert settings.wire_api == wire_api
    assert settings.temperature is None
    assert settings.max_tokens is None


def test_old_config_defaults_to_chat(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "default_provider: custom_6sol\nproviders:\n  custom_6sol:\n"
        "    base_url: https://relay.example/v1\n    model: test\n    api_key_env: TEST_KEY\n",
        encoding="utf-8",
    )
    settings = load_provider_settings(path)
    assert settings.wire_api == "chat"
    assert settings.temperature == 0.2
    assert settings.max_tokens == 2048


def test_current_sol_config_preserves_verified_request():
    settings = load_provider_settings(
        Path(__file__).parents[2] / "model_config.yaml", "custom_6sol"
    )
    assert settings.wire_api == "responses"
    assert settings.temperature is None
    assert settings.max_tokens is None
