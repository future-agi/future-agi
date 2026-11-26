"""FI_API_PUBLIC_URL: the API URL the in-app SDK snippet hands out as
FI_BASE_URL on a self-hosted install.

BASE_URL itself defaults to http://localhost:8000, which in-container callers
use. ./bin/install moves the published BACKEND_PORT when 8000 is taken and
keeps VITE_HOST_API in step, so an SDK outside the stack must follow those
rather than BASE_URL's default.
"""

import pytest

from tfc.settings.settings import _sdk_api_url

DEFAULT = "http://localhost:8000"


@pytest.mark.unit
class TestSdkApiUrl:
    def test_a_moved_port_without_base_url(self):
        assert _sdk_api_url({"BACKEND_PORT": "8001"}, DEFAULT) == (
            "http://localhost:8001"
        )

    def test_an_explicit_base_url_wins(self):
        environ = {
            "BASE_URL": "https://api.example.com",
            "VITE_HOST_API": "http://localhost:8001",
            "BACKEND_PORT": "8001",
        }

        assert _sdk_api_url(environ, DEFAULT) == "https://api.example.com"

    def test_the_ui_api_url_wins_over_the_port(self):
        environ = {"VITE_HOST_API": "https://api.example.com", "BACKEND_PORT": "8001"}

        assert _sdk_api_url(environ, DEFAULT) == "https://api.example.com"

    @pytest.mark.parametrize(
        "environ",
        [
            {},
            {"BASE_URL": "", "VITE_HOST_API": "", "BACKEND_PORT": ""},
            {"BASE_URL": "  ", "VITE_HOST_API": " ", "BACKEND_PORT": " "},
        ],
    )
    def test_falls_back_to_base_url(self, environ):
        assert _sdk_api_url(environ, DEFAULT) == DEFAULT

    @pytest.mark.parametrize(
        "environ, expected",
        [
            ({"BASE_URL": "https://api.example.com/"}, "https://api.example.com"),
            ({"VITE_HOST_API": "http://localhost:8001/"}, "http://localhost:8001"),
        ],
    )
    def test_strips_a_trailing_slash(self, environ, expected):
        assert _sdk_api_url(environ, DEFAULT) == expected

    def test_a_cloud_base_url_passes_through(self):
        cloud = "https://api.futureagi.com"

        assert _sdk_api_url({}, cloud) == cloud
