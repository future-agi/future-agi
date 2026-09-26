"""The keys block of the in-app SDK snippet (/tracer/project/project_sdk_code/).

Both SDKs default FI_BASE_URL to Future AGI Cloud. A self-hosted user who
copies the snippet after first sign-in must send spans, prompts and
completions included, to their own collector. No database needed.
"""

import os

import pytest

from tracer.views.project import sdk_key_snippets

COLLECTOR = "http://localhost:4319"


@pytest.fixture
def self_hosted(settings):
    settings.CLOUD_DEPLOYMENT = ""
    settings.FI_COLLECTOR_PUBLIC_URL = COLLECTOR


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

    def test_cloud_leaves_the_sdk_default(self, settings):
        settings.CLOUD_DEPLOYMENT = "US"

        for snippet in sdk_key_snippets().values():
            assert "FI_BASE_URL" not in snippet
            assert "YOUR_FI_API_KEY" in snippet
            assert "YOUR_FI_SECRET_KEY" in snippet
