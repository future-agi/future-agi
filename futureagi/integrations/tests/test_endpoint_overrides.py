"""The export services' endpoint overrides (integrations/services/endpoints.py).

Unset, each service builds its vendor's real URL; set, the same request goes to
the override. Nothing here sends a request: HTTP calls are patched and S3 calls
stop in botocore's before-send hook, after the URL is built.
"""

from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import pytest
from django.core.exceptions import ImproperlyConfigured

from integrations.services.cloud_storage_service import (
    CloudStorageService,
)
from integrations.services.datadog_service import DatadogService
from integrations.services.mixpanel_service import MixpanelService

OVERRIDES = [
    "INTEGRATIONS_DATADOG_URL",
    "INTEGRATIONS_MIXPANEL_URL",
    "INTEGRATIONS_S3_ENDPOINT_URL",
    "INTEGRATIONS_LINEAR_URL",
]


def _endpoints():
    from integrations.services import endpoints

    return endpoints


@pytest.fixture(autouse=True)
def _no_override(monkeypatch):
    """Every test starts with no override set, whatever the process carries."""
    for variable in OVERRIDES:
        monkeypatch.delenv(variable, raising=False)


def _response(status=200, text="", json_body=None):
    response = MagicMock(status_code=status, text=text)
    response.json.return_value = json_body or {}
    response.raise_for_status.return_value = None
    return response


class TestEndpointOverride:
    def test_unset_is_empty(self):
        assert all(_endpoints().endpoint_override(v) == "" for v in OVERRIDES)

    def test_set_drops_trailing_slash(self, monkeypatch):
        monkeypatch.setenv("INTEGRATIONS_DATADOG_URL", "http://sink.test:8080/r/x/")
        assert (
            _endpoints().endpoint_override("INTEGRATIONS_DATADOG_URL")
            == "http://sink.test:8080/r/x"
        )

    def test_unknown_variable_is_refused(self):
        with pytest.raises(KeyError):
            _endpoints().endpoint_override("INTEGRATIONS_OTHER_URL")

    def test_startup_line_names_variable_and_host_only(self, monkeypatch):
        monkeypatch.setenv(
            "INTEGRATIONS_DATADOG_URL", "http://sink.test:8080/r/dd-secret-path"
        )
        logger = MagicMock()
        _endpoints().log_endpoint_overrides(logger)
        logger.warning.assert_called_once_with(
            "integration_endpoint_override",
            variable="INTEGRATIONS_DATADOG_URL",
            destination="Datadog",
            host="sink.test:8080",
        )
        assert "dd-secret-path" not in repr(logger.mock_calls)

    @pytest.mark.parametrize(
        "value, rule",
        [
            ("http://user:hunter2@sink.test:8080/r/dd", "no credentials"),
            ("http://sink.test/a\nb", "no whitespace"),
            ("http://sink.test/a\rb", "no whitespace"),
            ("http://sink.test/a\tb", "no whitespace"),
            ("http://si\tnk.test/a", "no whitespace"),
            ("http://sink.test/a b", "no whitespace"),
            (" http://sink.test/a", "no whitespace"),
            ("http://hunter2@sink.test/r/dd", "no credentials"),
            ("http://sink.test/r/dd?token=hunter2", "no query or fragment"),
            ("http://sink.test/r/dd#hunter2", "no query or fragment"),
            ("ftp://sink.test/hunter2", "http or https URL with a host"),
            ("sink.test:8080/hunter2", "http or https URL with a host"),
            ("http:///hunter2", "http or https URL with a host"),
            ("http://sink.test:hunter2/r", "valid host and port"),
            ("http://sink.test:99999/hunter2", "valid host and port"),
        ],
    )
    def test_a_malformed_value_is_refused_without_its_value(
        self, monkeypatch, value, rule
    ):
        monkeypatch.setenv("INTEGRATIONS_DATADOG_URL", value)
        with pytest.raises(ImproperlyConfigured) as refused:
            _endpoints().endpoint_override("INTEGRATIONS_DATADOG_URL")
        message = str(refused.value)
        assert message.startswith("INTEGRATIONS_DATADOG_URL ")
        assert rule in message
        assert "hunter2" not in message
        # No chained exception carries the value into a traceback.
        assert refused.value.__cause__ is None
        assert refused.value.__context__ is None or refused.value.__suppress_context__

    def test_a_malformed_value_stops_the_process_at_start(self, monkeypatch):
        monkeypatch.setenv("INTEGRATIONS_S3_ENDPOINT_URL", "http://k:hunter2@sink:9000")
        logger = MagicMock()
        with pytest.raises(ImproperlyConfigured, match="INTEGRATIONS_S3_ENDPOINT_URL"):
            _endpoints().log_endpoint_overrides(logger)
        assert "hunter2" not in repr(logger.mock_calls)

    def test_no_line_when_unset(self):
        logger = MagicMock()
        _endpoints().log_endpoint_overrides(logger)
        logger.warning.assert_not_called()


class TestDatadog:
    CREDS = {"api_key": "e2e-dd-key", "site": "us1"}

    @patch("integrations.services.datadog_service.requests")
    def test_unset_calls_the_sites_real_hosts(self, requests_mock):
        requests_mock.get.return_value = _response(200)
        requests_mock.post.return_value = _response(202)
        service = DatadogService()

        assert service.validate_credentials("", self.CREDS)["valid"] is True
        service.export_logs(self.CREDS, [{"message": "m"}])
        service.export_metrics(self.CREDS, [{"metric": "x"}])

        assert requests_mock.get.call_args.args[0] == (
            "https://api.datadoghq.com/api/v1/validate"
        )
        posted = [c.args[0] for c in requests_mock.post.call_args_list]
        assert posted == [
            "https://http-intake.logs.datadoghq.com/api/v2/logs",
            "https://api.datadoghq.com/api/v2/series",
        ]

    @patch("integrations.services.datadog_service.requests")
    def test_unset_follows_the_site(self, requests_mock):
        requests_mock.post.return_value = _response(202)
        DatadogService().export_logs(
            {"api_key": "k", "site": "eu1"}, [{"message": "m"}]
        )
        assert requests_mock.post.call_args.args[0] == (
            "https://http-intake.logs.datadoghq.eu/api/v2/logs"
        )

    @patch("integrations.services.datadog_service.requests")
    def test_set_sends_every_call_to_the_override(self, requests_mock, monkeypatch):
        monkeypatch.setenv("INTEGRATIONS_DATADOG_URL", "http://sink.test:8080/r/dd")
        requests_mock.get.return_value = _response(200)
        requests_mock.post.return_value = _response(202)
        service = DatadogService()

        service.validate_credentials("", self.CREDS)
        service.export_logs(self.CREDS, [{"message": "m"}])
        service.export_metrics(self.CREDS, [{"metric": "x"}])

        assert requests_mock.get.call_args.args[0] == (
            "http://sink.test:8080/r/dd/api/v1/validate"
        )
        assert [c.args[0] for c in requests_mock.post.call_args_list] == [
            "http://sink.test:8080/r/dd/api/v2/logs",
            "http://sink.test:8080/r/dd/api/v2/series",
        ]


class TestMixpanel:
    @patch("integrations.services.mixpanel_service.requests")
    def test_unset_calls_mixpanel(self, requests_mock):
        requests_mock.post.return_value = _response(200, text="1")
        service = MixpanelService()

        assert service.validate_credentials("", {"project_token": "t"})["valid"]
        service.export_events({"project_token": "t"}, [{"event": "e"}])
        service.export_events(
            {"project_token": "t", "api_secret": "s"}, [{"event": "e"}]
        )

        assert [c.args[0] for c in requests_mock.post.call_args_list] == [
            "https://api.mixpanel.com/track",
            "https://api.mixpanel.com/track",
            "https://api.mixpanel.com/import",
        ]

    @patch("integrations.services.mixpanel_service.requests")
    def test_set_sends_every_call_to_the_override(self, requests_mock, monkeypatch):
        monkeypatch.setenv("INTEGRATIONS_MIXPANEL_URL", "http://sink.test:8080/r/mp")
        requests_mock.post.return_value = _response(200, text="1")
        service = MixpanelService()

        service.validate_credentials("", {"project_token": "t"})
        service.export_events(
            {"project_token": "t", "api_secret": "s"}, [{"event": "e"}]
        )

        assert [c.args[0] for c in requests_mock.post.call_args_list] == [
            "http://sink.test:8080/r/mp/track",
            "http://sink.test:8080/r/mp/import",
        ]


class TestLinear:
    CREDS = {"api_key": "lin_api_e2e"}
    TEAMS = {
        "data": {
            "teams": {"nodes": [{"id": "t1", "name": "E2E", "key": "E2E"}]},
            "viewer": {"id": "u1", "name": "E2E", "email": "e2e@example.test"},
        }
    }
    ISSUE = {
        "data": {
            "issueCreate": {
                "success": True,
                "issue": {
                    "id": "i1",
                    "identifier": "E2E-1",
                    "url": "https://linear.app/e2e/issue/E2E-1",
                    "title": "t",
                },
            }
        }
    }

    def _calls(self, requests_mock):
        return [c.args[0] for c in requests_mock.post.call_args_list]

    @patch("integrations.services.linear_service.requests")
    def test_unset_calls_linear(self, requests_mock):
        from integrations.services.linear_service import LinearService

        requests_mock.post.side_effect = [
            _response(json_body=self.TEAMS),
            _response(json_body=self.TEAMS),
            _response(json_body=self.ISSUE),
        ]
        service = LinearService()
        assert service.validate_credentials("", self.CREDS)["valid"] is True
        service.get_teams(self.CREDS)
        service.create_issue(self.CREDS, team_id="t1", title="t")
        assert self._calls(requests_mock) == ["https://api.linear.app/graphql"] * 3
        headers = requests_mock.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "lin_api_e2e"

    @patch("integrations.services.linear_service.requests")
    def test_set_sends_every_call_to_the_override(self, requests_mock, monkeypatch):
        from integrations.services.linear_service import LinearService

        monkeypatch.setenv("INTEGRATIONS_LINEAR_URL", "http://sink.test:8080/r/ln/")
        requests_mock.post.side_effect = [
            _response(json_body=self.TEAMS),
            _response(json_body=self.TEAMS),
            _response(json_body=self.ISSUE),
        ]
        service = LinearService()
        service.validate_credentials("", self.CREDS)
        service.get_teams(self.CREDS)
        service.create_issue(self.CREDS, team_id="t1", title="t")
        assert self._calls(requests_mock) == ["http://sink.test:8080/r/ln/graphql"] * 3

    def test_the_override_is_listed_for_linear(self):
        assert _endpoints().ENDPOINT_OVERRIDES["INTEGRATIONS_LINEAR_URL"] == "Linear"


class _Stop(Exception):
    """Raised from before-send: the request was built and is never sent."""


def _put_object_url(credentials):
    from integrations.services.cloud_storage_service import _s3_client

    client = _s3_client(credentials)
    seen = {}

    def capture(request, **_):
        seen["url"] = request.url
        raise _Stop

    client.meta.events.register("before-send.s3.PutObject", capture)
    with pytest.raises(_Stop):
        client.put_object(
            Bucket=credentials["bucket"], Key="logs/a.jsonl.gz", Body=b"x"
        )
    return client, seen["url"]


class TestS3:
    CREDS = {
        "sub_platform": "s3",
        "bucket": "e2e-exports",
        "region": "eu-west-1",
        "access_key_id": "e2e-access",
        "secret_access_key": "e2e-secret",
    }

    def test_unset_addresses_amazon_s3(self):
        client, url = _put_object_url(self.CREDS)
        parts = urlsplit(url)
        assert parts.scheme == "https"
        assert parts.hostname == "e2e-exports.s3.eu-west-1.amazonaws.com"
        assert parts.path == "/logs/a.jsonl.gz"
        assert urlsplit(client.meta.endpoint_url).hostname.endswith(".amazonaws.com")

    def test_set_addresses_the_override_path_style(self, monkeypatch):
        monkeypatch.setenv("INTEGRATIONS_S3_ENDPOINT_URL", "http://storage-sink:9000/")
        client, url = _put_object_url(self.CREDS)
        assert url == "http://storage-sink:9000/e2e-exports/logs/a.jsonl.gz"
        assert client.meta.endpoint_url == "http://storage-sink:9000"

    def test_export_uploads_through_the_same_client(self, monkeypatch):
        monkeypatch.setenv("INTEGRATIONS_S3_ENDPOINT_URL", "http://storage-sink:9000")
        client = MagicMock()
        with patch(
            "integrations.services.cloud_storage_service._s3_client",
            return_value=client,
        ) as factory:
            result = CloudStorageService().export_logs(
                {**self.CREDS, "prefix": "p"}, [{"request_id": "r1"}]
            )
        factory.assert_called_once()
        put = client.put_object.call_args.kwargs
        assert put["Bucket"] == "e2e-exports"
        assert put["Key"] == result["object_key"]
        assert put["Key"].startswith("p/logs/")
        assert result["sent"] == 1


def test_refused_override_is_absent_from_product_exception_locals(monkeypatch):
    import traceback

    monkeypatch.setenv(
        "INTEGRATIONS_DATADOG_URL", "http://dummy:synthetic-password@sink.test"
    )
    with pytest.raises(ImproperlyConfigured) as caught:
        _endpoints().endpoint_override("INTEGRATIONS_DATADOG_URL")
    for frame, _ in traceback.walk_tb(caught.value.__traceback__):
        if frame.f_code.co_filename == _endpoints().__file__:
            assert "synthetic-password" not in repr(frame.f_locals)
