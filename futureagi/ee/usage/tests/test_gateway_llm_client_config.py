"""Cloud gateway config: the in-network default must be the container port.

The gateway listens on 8080 inside the network (compose maps host 8090 ->
8080; the k8s service is 8080). 8090 is refused container-to-container.
"""

from unittest.mock import patch

from ee.usage.services.gateway_llm_client import _resolve_gateway_config


def test_cloud_default_gateway_url_uses_the_container_port():
    with patch(
        "ee.usage.services.gateway_llm_client._get_setting",
        side_effect=lambda name, default="": {
            "AGENTCC_INTERNAL_API_KEY": "internal-key",
        }.get(name, default),
    ):
        base_url, api_key, mode = _resolve_gateway_config()

    assert mode == "cloud"
    assert base_url == "http://agentcc-gateway:8080"
    assert api_key == "internal-key"
