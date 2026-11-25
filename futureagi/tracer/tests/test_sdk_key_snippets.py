"""The keys block of the in-app SDK snippet (/tracer/project/project_sdk_code/).

Both SDKs default to Future AGI Cloud. A self-hosted user who copies the
snippet after first sign-in must send spans, prompts and completions included,
to their own collector (FI_TRACES_URL) and point API calls at their own API
(FI_BASE_URL). No database needed.
"""

import os

import pytest

from tracer.views.project import sdk_key_snippets

API = "http://localhost:8001"
COLLECTOR = "http://localhost:4319"
SDK_URL_VARIABLES = ("FI_BASE_URL", "FI_TRACES_URL")


@pytest.fixture
def self_hosted(settings):
    settings.CLOUD_DEPLOYMENT = ""
    settings.BASE_URL = "http://localhost:8000"
    settings.FI_API_PUBLIC_URL = API
    settings.FI_COLLECTOR_PUBLIC_URL = COLLECTOR


@pytest.mark.unit
class TestSdkKeySnippets:
    def test_self_hosted_points_both_sdks_at_its_api_and_collector(self, self_hosted):
        snippets = sdk_key_snippets()

        assert f'os.environ["FI_BASE_URL"] = "{API}"' in snippets["Python"]
        assert f'os.environ["FI_TRACES_URL"] = "{COLLECTOR}"' in snippets["Python"]
        assert f'process.env.FI_BASE_URL = "{API}";' in snippets["TypeScript"]
        assert f'process.env.FI_TRACES_URL = "{COLLECTOR}";' in snippets["TypeScript"]

    def test_names_the_sdk_versions_that_read_the_traces_url(self, self_hosted):
        snippets = sdk_key_snippets()

        assert "fi-instrumentation-otel 1.5.0+" in snippets["Python"]
        assert "@traceai/fi-core 1.4.0+" in snippets["TypeScript"]
        for snippet in snippets.values():
            assert "set FI_BASE_URL to the collector URL instead" in snippet

    def test_the_api_url_follows_a_moved_port_not_base_url(self, self_hosted):
        """BASE_URL keeps its in-container default; the published port moved."""
        snippets = sdk_key_snippets()

        assert "localhost:8000" not in snippets["Python"]
        assert f'os.environ["FI_BASE_URL"] = "{API}"' in snippets["Python"]

    def test_strips_trailing_slashes(self, self_hosted, settings):
        settings.FI_API_PUBLIC_URL = f"{API}/"
        settings.FI_COLLECTOR_PUBLIC_URL = f"{COLLECTOR}/"

        snippets = sdk_key_snippets()

        assert f'os.environ["FI_BASE_URL"] = "{API}"' in snippets["Python"]
        assert f'process.env.FI_TRACES_URL = "{COLLECTOR}";' in snippets["TypeScript"]

    def test_the_python_snippet_runs(self, self_hosted, monkeypatch):
        for key in ("FI_API_KEY", "FI_SECRET_KEY", *SDK_URL_VARIABLES):
            monkeypatch.delenv(key, raising=False)

        exec(sdk_key_snippets()["Python"], {})  # noqa: S102

        assert os.environ["FI_BASE_URL"] == API
        assert os.environ["FI_TRACES_URL"] == COLLECTOR
        assert os.environ["FI_API_KEY"] == "YOUR_FI_API_KEY"

    @pytest.mark.parametrize("deployment", ["false", "self-hosted"])
    def test_only_a_cloud_region_counts_as_cloud(
        self, self_hosted, settings, deployment
    ):
        settings.CLOUD_DEPLOYMENT = deployment

        snippet = sdk_key_snippets()["Python"]
        for variable in SDK_URL_VARIABLES:
            assert variable in snippet

    def test_cloud_leaves_the_sdk_default(self, settings):
        settings.CLOUD_DEPLOYMENT = "US"

        for snippet in sdk_key_snippets().values():
            for variable in SDK_URL_VARIABLES:
                assert variable not in snippet
            assert "YOUR_FI_API_KEY" in snippet
            assert "YOUR_FI_SECRET_KEY" in snippet
