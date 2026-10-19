import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from newspaper_agent.app import main


CONFIG = Path(__file__).parents[2] / "model_config.yaml"


@pytest.mark.parametrize("provider,env_name,model", [
    ("deepseek", "DEEPSEEK_API_KEY", "deepseek-chat"),
    ("openai", "OPENAI_API_KEY", "gpt-4o-mini"),
    ("custom", "NEWSPAPER_AGENT_CUSTOM_API_KEY", "example-model"),
])
def test_cli_factory_http_response_round_trip(monkeypatch, capsys, tmp_path, provider, env_name, model):
    monkeypatch.setenv(env_name, "offline-test-key")
    monkeypatch.chdir(tmp_path)
    # 测试使用自己的配置，不依赖开发者本地 provider 名称或模型。
    config = tmp_path / "model_config.yaml"
    config.write_text(
        f"default_provider: {provider}\nproviders:\n  {provider}:\n"
        f"    base_url: https://relay.example/v1\n    model: {model}\n"
        f"    api_key_env: {env_name}\n",
        encoding="utf-8",
    )
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["model"] == model
        assert body["messages"] == [{"role": "user", "content": "你好"}]
        assert request.headers["authorization"] == "Bearer offline-test-key"
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "模拟服务回复"}}]})

    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handler), timeout=kwargs["timeout"]))
    main(["--mode", "live", "--provider", provider, "--config", str(config), "--message", "你好"])
    assert capsys.readouterr().out.strip() == "模拟服务回复"
    assert len(requests) == 1


@pytest.mark.parametrize("provider", ["custom_6sol", "renamed-relay"])
def test_configured_cli_uses_responses(monkeypatch, capsys, tmp_path, provider):
    monkeypatch.setenv("NEWSPAPER_AGENT_CUSTOM_API_KEY", "offline-test-key")
    config = tmp_path / "responses.yaml"
    config.write_text(
        f"providers:\n  {provider}:\n    base_url: https://relay.example/v1\n"
        "    model: gpt-6-sol\n    api_key_env: NEWSPAPER_AGENT_CUSTOM_API_KEY\n"
        "    wire_api: responses\n    temperature: null\n    max_tokens: null\n",
        encoding="utf-8",
    )
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == "https://relay.example/v1/responses"
        assert json.loads(request.content) == {
            "model": "gpt-6-sol",
            "input": [{"role": "user", "content": "你好"}],
            "store": False,
            "stream": False,
        }
        return httpx.Response(200, json={
            "status": "completed",
            "output": [{
                "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "Responses 模拟回复"}],
            }],
        })

    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(
        transport=httpx.MockTransport(handler), timeout=kwargs["timeout"]
    ))
    main(["--mode", "live", "--provider", provider, "--config", str(config), "--message", "你好"])
    assert capsys.readouterr().out.strip() == "Responses 模拟回复"
    assert len(requests) == 1


def test_cli_http_failure_exits_cleanly(monkeypatch, capsys):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(lambda _: httpx.Response(401))))
    with pytest.raises(SystemExit) as error:
        main(["--mode", "live", "--config", str(CONFIG)])
    assert error.value.code == 1
    output = capsys.readouterr().err
    assert "HTTP 401" in output
    assert "offline-test-key" not in output


def test_installed_factory_imports_outside_repo(tmp_path):
    result = subprocess.run([sys.executable, "-c", "from newspaper_agent.model.factory import create_model_client"], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
