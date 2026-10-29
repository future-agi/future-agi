import pytest

from agentcc.services.url_safety import WEBHOOK_PRIVATE_URL_ERROR
from integrations.services.langfuse_service import LangfuseService
from integrations.services.posthog_service import PostHogService


@pytest.mark.parametrize(
    "host",
    [
        "http://169.254.169.254",
        "http://127.0.0.1",
        "http://10.0.0.5",
    ],
)
def test_langfuse_refuses_private_host(host):
    with pytest.raises(ValueError, match=WEBHOOK_PRIVATE_URL_ERROR):
        LangfuseService()._make_request(
            "GET",
            host,
            "/api/public/projects",
            {"public_key": "pk", "secret_key": "sk"},
        )


def test_posthog_refuses_private_host():
    result = PostHogService().validate_credentials(
        "http://169.254.169.254",
        {"api_key": "phc_test"},
    )
    assert result["valid"] is False
    assert WEBHOOK_PRIVATE_URL_ERROR in result["error"]
