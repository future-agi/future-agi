"""The keys block of the in-app SDK snippet (/tracer/project/project_sdk_code/).

Both SDKs default FI_BASE_URL to Future AGI Cloud. A self-hosted user who
copies the snippet after first sign-in must send spans, prompts and
completions included, to their own collector. No database needed.
"""

import os

import pytest

import tracer.views.project as project_view
from tracer.views.project import sdk_key_snippets

COLLECTOR = "http://localhost:4319"
# Read through the module inside the tests below, so a build without the
# placeholder fails those tests and not the whole file.
PLACEHOLDER = "YOUR_FI_COLLECTOR_URL"


@pytest.fixture
def self_hosted(settings):
    # An install used on the machine it runs on: the UI is on localhost too.
    settings.CLOUD_DEPLOYMENT = ""
    settings.APP_URL = "localhost:3000"  # the bare host[:port] settings produce
    settings.FI_COLLECTOR_PUBLIC_URL = COLLECTOR


@pytest.fixture
def hosted(settings):
    # A self-hosted deployment people reach through a public address.
    settings.CLOUD_DEPLOYMENT = ""
    settings.APP_URL = "observe.example.com"


@pytest.mark.unit
class TestSdkKeySnippets:
    def test_self_hosted_points_both_sdks_at_its_collector(self, self_hosted):
        snippets = sdk_key_snippets()

        assert f'os.environ["FI_BASE_URL"] = "{COLLECTOR}"' in snippets["Python"]
        assert f'process.env.FI_BASE_URL = "{COLLECTOR}";' in snippets["TypeScript"]

    def test_the_python_snippet_runs(self, self_hosted, monkeypatch):
        for key in ("FI_API_KEY", "FI_SECRET_KEY", "FI_BASE_URL"):
            monkeypatch.delenv(key, raising=False)

        exec(sdk_key_snippets()["Python"], {})  # noqa: S102

        assert os.environ["FI_BASE_URL"] == COLLECTOR
        assert os.environ["FI_API_KEY"] == "YOUR_FI_API_KEY"

    @pytest.mark.parametrize("deployment", ["false", "self-hosted"])
    def test_only_a_cloud_region_counts_as_cloud(
        self, self_hosted, settings, deployment
    ):
        settings.CLOUD_DEPLOYMENT = deployment

        assert "FI_BASE_URL" in sdk_key_snippets()["Python"]

    def test_cloud_leaves_the_sdk_default(self, settings):
        settings.CLOUD_DEPLOYMENT = "US"

        for snippet in sdk_key_snippets().values():
            assert "FI_BASE_URL" not in snippet
            assert "YOUR_FI_API_KEY" in snippet
            assert "YOUR_FI_SECRET_KEY" in snippet


@pytest.mark.unit
class TestHostedDeploymentWithoutACollectorAddress:
    """A hosted deployment whose FI_COLLECTOR_PUBLIC_URL is unset falls back to
    http://localhost:<port>. Printing that sends a reader's spans to their own
    machine and leaves the setup page waiting forever."""

    @pytest.mark.parametrize(
        "fallback", ["http://localhost:4320", "http://127.0.0.1:4318", ""]
    )
    def test_prints_a_placeholder_not_a_local_address(self, hosted, settings, fallback):
        settings.FI_COLLECTOR_PUBLIC_URL = fallback

        snippets = sdk_key_snippets()

        assert f'os.environ["FI_BASE_URL"] = "{PLACEHOLDER}"' in snippets["Python"]
        assert f'process.env.FI_BASE_URL = "{PLACEHOLDER}";' in snippets["TypeScript"]
        for snippet in snippets.values():
            assert "localhost" not in snippet
            assert "127.0.0.1" not in snippet
        assert project_view.collector_url_missing() is True
        assert project_view.COLLECTOR_URL_PLACEHOLDER == PLACEHOLDER

    def test_never_drops_the_line(self, hosted, settings):
        # Without FI_BASE_URL both SDKs send to Future AGI Cloud.
        settings.FI_COLLECTOR_PUBLIC_URL = "http://localhost:4320"

        for snippet in sdk_key_snippets().values():
            assert "FI_BASE_URL" in snippet

    def test_a_published_collector_is_printed(self, hosted, settings):
        settings.FI_COLLECTOR_PUBLIC_URL = "https://otlp.observe.example.com"

        snippets = sdk_key_snippets()

        assert (
            'os.environ["FI_BASE_URL"] = "https://otlp.observe.example.com"'
            in snippets["Python"]
        )
        assert PLACEHOLDER not in snippets["Python"]
        assert project_view.collector_url_missing() is False

    def test_a_local_install_keeps_its_local_collector(self, self_hosted):
        assert project_view.collector_url_missing() is False
        assert f'"{COLLECTOR}"' in sdk_key_snippets()["Python"]

    @pytest.mark.parametrize(
        "app_url", ["localhost:3031", "127.0.1.1:3000", "[::1]:3000", "0.0.0.0:3000"]
    )
    def test_any_local_app_address_keeps_the_local_collector(self, settings, app_url):
        settings.CLOUD_DEPLOYMENT = ""
        settings.APP_URL = app_url
        settings.FI_COLLECTOR_PUBLIC_URL = "http://localhost:4318"

        assert project_view.collector_url_missing() is False
        assert '"http://localhost:4318"' in sdk_key_snippets()["Python"]

    def test_an_app_url_with_a_scheme_is_read_the_same_way(self, settings):
        settings.CLOUD_DEPLOYMENT = ""
        settings.APP_URL = "https://observe.example.com"
        settings.FI_COLLECTOR_PUBLIC_URL = "http://localhost:4320"

        assert project_view.collector_url_missing() is True
