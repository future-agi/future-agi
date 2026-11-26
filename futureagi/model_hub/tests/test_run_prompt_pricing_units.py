"""Real HTTP preview preserves character units and labels token-only estimates."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from accounts.models import OrgApiKey
from model_hub.models.api_key import ApiKey
from model_hub.models.develop_dataset import Dataset, Row


@pytest.fixture
def character_usage_provider():
    observed_usage = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            scenario = request["messages"][-1]["content"]
            if isinstance(scenario, list):
                scenario = "".join(part.get("text", "") for part in scenario)
            assert scenario in {"explicit characters", "tokens only"}, scenario
            usage = {
                "prompt_tokens": 10000,
                "completion_tokens": 2000,
                "total_tokens": 12000,
            }
            if scenario == "explicit characters":
                usage.update(input_characters=50000, output_characters=10000)
            observed_usage.append(usage)
            payload = {
                "id": "synthetic-completion",
                "object": "chat.completion",
                "created": 1,
                # Exercise the catalog unit contract, not an actual Gemini invoice.
                "model": "gemini-1.5-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Synthetic reply"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": usage,
            }
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1", observed_usage
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("scenario", ["explicit characters", "tokens only"])
def test_preview_api_preserves_character_usage_or_labels_estimate(
    live_server, user, workspace, character_usage_provider, scenario
):
    provider_url, observed_usage = character_usage_provider
    organization = user.organization
    key = OrgApiKey.objects.create(
        name="Synthetic pricing check",
        api_key="synthetic-pricing-api",
        secret_key="synthetic-pricing-secret",
        type="user",
        user=user,
        organization=organization,
        workspace=workspace,
    )
    ApiKey.objects.create(
        provider="openai",
        organization=organization,
        workspace=workspace,
        config_json={
            "key": "synthetic-local-provider",
            "api_base": provider_url,
        },
    )
    dataset = Dataset.objects.create(
        name="Synthetic pricing data",
        organization=organization,
        workspace=workspace,
        user=user,
    )
    Row.objects.create(dataset=dataset, order=1)
    response = requests.post(
        f"{live_server.url}/model-hub/develops/preview_run_prompt_column/",
        headers={
            "X-Api-Key": key.api_key,
            "X-Secret-Key": key.secret_key,
            "X-Workspace-Id": str(workspace.id),
            "X-Organization-Id": str(organization.id),
        },
        json={
            "dataset_id": str(dataset.id),
            "name": "Synthetic preview",
            "first_n_rows": 1,
            "config": {
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": scenario}],
                "output_format": "string",
                "max_tokens": 20,
            },
        },
        timeout=30,
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    print(
        "LOCAL_API_EVIDENCE "
        + json.dumps(
            {
                "scenario": scenario,
                "http_status": response.status_code,
                "upstream_usage": observed_usage,
                "result": result,
            },
            sort_keys=True,
        )
    )
    assert result["responses"] == ["Synthetic reply"], result
    assert len(observed_usage) == 1
    if scenario == "explicit characters":
        assert observed_usage[0]["input_characters"] == 50000
        assert observed_usage[0]["output_characters"] == 10000
        assert result["token_usage"]["input_characters"] == 50000
        assert result["token_usage"]["output_characters"] == 10000
        assert result["cost"]["pricing_source"] == "available_models", result
        assert result["cost"]["total_cost"] == 0.42, result
    else:
        assert "input_characters" not in observed_usage[0]
        assert "output_characters" not in observed_usage[0]
        assert result["cost"]["pricing_source"] == "default", result
        assert result["cost"]["total_cost"] == 0.0027, result
        assert "input_characters" not in result["token_usage"]
        assert "output_characters" not in result["token_usage"]
