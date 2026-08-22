from pathlib import Path

from config.settings import load_provider_settings


def test_model_config_has_no_secret_value():
    settings = load_provider_settings(Path(__file__).parents[2] / "model_config.yaml")
    assert settings.api_key_env == "DEEPSEEK_API_KEY"
    assert settings.base_url == "https://api.deepseek.com/v1"
    assert settings.model == "deepseek-chat"


def test_openai_provider_is_available():
    settings = load_provider_settings(Path(__file__).parents[2] / "model_config.yaml", provider_name="openai")
    assert settings.api_key_env == "OPENAI_API_KEY"
    assert settings.base_url == "https://api.openai.com/v1"
