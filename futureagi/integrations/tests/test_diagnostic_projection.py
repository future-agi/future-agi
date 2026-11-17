"""Diagnostic surfaces are data boundaries, including historical rows.

Synthetic input only; no network, vendor, Sentry transport or real credentials.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests
import structlog
from botocore.exceptions import ClientError
from django.utils import timezone

from integrations.models import SyncLog
from integrations.serializers.sync_log import SyncLogSerializer


def describe_export_failure(*args, **kwargs):
    from integrations.services.export_failures import (
        describe_export_failure as describe,
    )

    return describe(*args, **kwargs)


MARKER = "synthetic-sensitive-fragment"
UNSAFE = f"https://user:{MARKER}@destination.invalid/path?token={MARKER}\r\nforged"


def connection(platform="datadog"):
    return SimpleNamespace(
        platform=platform, host_url=UNSAFE, get_platform_display=lambda: "Datadog"
    )


@pytest.mark.parametrize("code", [400, 422, 500])
def test_http_message_has_category_status_not_vendor_text(code):
    response = requests.Response()
    response.status_code = code
    response.reason = UNSAFE
    response._content = json.dumps({"message": UNSAFE}).encode()
    error = requests.HTTPError(UNSAFE, response=response)
    failure = describe_export_failure(connection(), error)
    assert MARKER not in failure.message
    assert "\r" not in failure.message and "\n" not in failure.message
    assert str(code) in failure.message
    assert failure.details(error)["http_status"] == code


def test_upload_message_does_not_echo_unknown_vendor_code():
    error = ClientError(
        {
            "Error": {"Code": UNSAFE, "Message": UNSAFE},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        "PutObject",
    )
    failure = describe_export_failure(connection("cloud_storage"), error)
    assert MARKER not in failure.message
    assert failure.details(error)["vendor_code"] is None


@pytest.mark.django_db
@pytest.mark.parametrize("field", ["kind", "type", "http_status", "vendor_code"])
def test_historical_allowed_keys_do_not_trust_values(integration_connection, field):
    log = SyncLog.objects.create(
        connection=integration_connection,
        started_at=timezone.now(),
        status="failed",
        error_message=UNSAFE,
        error_details={field: UNSAFE},
    )
    assert MARKER not in json.dumps(SyncLogSerializer(log).data, default=str)


@pytest.mark.parametrize(
    "service,path,credentials",
    [
        ("DatadogService", "datadog_service", {"api_key": "dummy"}),
        ("LinearService", "linear_service", {"api_key": "dummy"}),
        ("PagerDutyService", "pagerduty_service", {"routing_key": "dummy"}),
        ("PostHogService", "posthog_service", {"api_key": "dummy"}),
        ("MixpanelService", "mixpanel_service", {"project_token": "dummy"}),
        (
            "LangfuseService",
            "langfuse_service",
            {"public_key": "dummy", "secret_key": "dummy"},
        ),
    ],
)
def test_validator_never_returns_or_logs_exception(service, path, credentials, caplog):
    from importlib import import_module

    module = import_module(f"integrations.services.{path}")
    with (
        patch.object(module.requests, "get", side_effect=ValueError(UNSAFE)),
        patch.object(module.requests, "post", side_effect=ValueError(UNSAFE)),
        patch.object(module.requests, "request", side_effect=ValueError(UNSAFE)),
    ):
        result = getattr(module, service)().validate_credentials(
            "https://local.invalid", credentials
        )
    assert result["valid"] is False
    assert MARKER not in json.dumps(result)
    assert MARKER not in caplog.text


def test_s3_validation_does_not_return_vendor_message(caplog):
    from integrations.services.cloud_storage_service import CloudStorageService

    error = ClientError(
        {
            "Error": {"Code": "SignatureDoesNotMatch", "Message": UNSAFE},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        },
        "PutObject",
    )
    with patch("boto3.client", side_effect=error):
        result = CloudStorageService().validate_credentials(
            "",
            {
                "sub_platform": "s3",
                "bucket": "fixture",
                "access_key_id": "dummy",
                "secret_access_key": "dummy",
            },
        )
    assert result["valid"] is False
    assert "SignatureDoesNotMatch" in result["error"]
    assert MARKER not in json.dumps(result)
    assert MARKER not in caplog.text


@pytest.mark.django_db
@pytest.mark.parametrize("vendor_status,api_status", [(200, 200), (401, 400)])
def test_validation_request_log_omits_private_path_but_transport_keeps_it(
    auth_client, vendor_status, api_status
):
    # Exercise the real endpoint and validator, not just the failure helper.
    # A self-hosted base path is supported request data, never log metadata.
    host = f"https://selfhost.invalid/{MARKER}/posthog"
    with (
        patch(
            "requests.adapters.HTTPAdapter.send",
            side_effect=AssertionError("Unmocked transport is forbidden"),
        ),
        patch(
            "integrations.services.posthog_service.requests.post",
            return_value=Mock(status_code=vendor_status),
        ) as post,
        structlog.testing.capture_logs() as logs,
    ):
        response = auth_client.post(
            "/integrations/connections/validate/",
            {
                "platform": "posthog",
                "host_url": host,
                "credentials": {"api_key": "dummy"},
            },
            format="json",
        )
    assert response.status_code == api_status
    post.assert_called_once_with(
        f"{host}/decide/?v=3",
        json={"api_key": "dummy", "distinct_id": "agentcc_connection_test"},
        timeout=15,
    )
    request_logs = [
        row for row in logs if row.get("event") == "Validate credentials request"
    ]
    assert len(request_logs) == 1
    assert request_logs[0]["platform"] == "posthog"
    assert request_logs[0]["log_level"] == "info"
    assert MARKER not in json.dumps(request_logs)
    assert MARKER not in json.dumps(logs, default=str)
    if api_status == 200:
        assert response.json()["result"]["valid"] is True
    else:
        assert "Invalid API key" in response.content.decode()


@pytest.mark.parametrize("platform", ["s3", "sqs"])
def test_storage_queue_outer_exception_is_safe(platform, caplog):
    from integrations.services.cloud_storage_service import CloudStorageService
    from integrations.services.queue_service import QueueService

    service = CloudStorageService() if platform == "s3" else QueueService()
    with patch.object(service, f"_validate_{platform}", side_effect=ValueError(UNSAFE)):
        result = service.validate_credentials("", {"sub_platform": platform})
    assert result["valid"] is False
    assert MARKER not in json.dumps(result)
    assert MARKER not in caplog.text
