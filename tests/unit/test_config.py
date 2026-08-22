from pathlib import Path

from config.settings import load_provider_settings


def test_model_config_has_no_secret_value():
    settings = load_provider_settings(Path(__file__).parents[2] / "model_config.yaml")
    assert settings.api_key_env == "NEWSPAPER_AGENT_MODEL_API_KEY"
    assert settings.model == "example-model"
