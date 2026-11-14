"""APP_URL and APP_BASE_URL: the UI address that links leaving the app use.

APP_URL is documented as a bare host (the compose files and the Helm chart
default it to ``localhost:<ui port>``), but operators also write it with a
scheme. Invite, reset and email links are built from APP_BASE_URL, which must
be an absolute URL either way.
"""

import pytest

from tfc.settings.settings import _app_base_url, _split_app_url


@pytest.mark.unit
class TestSplitAppUrl:
    @pytest.mark.parametrize(
        "value, expected",
        [
            ("localhost:3000", ("", "localhost:3000")),
            ("app.example.com", ("", "app.example.com")),
            ("https://app.example.com", ("https", "app.example.com")),
            ("http://10.0.0.5:3000/", ("http", "10.0.0.5:3000")),
            ("  app.example.com/  ", ("", "app.example.com")),
            ("", ("", None)),
            (None, ("", None)),
        ],
    )
    def test_with_or_without_a_scheme(self, value, expected):
        assert _split_app_url(value) == expected


@pytest.mark.unit
class TestAppBaseUrl:
    @pytest.mark.parametrize(
        "app_url, default_scheme, expected",
        [
            # A loopback UI has no certificate, whatever ENV_TYPE says (a
            # Helm install reached through a port-forward runs as production).
            ("localhost:3000", "https://", "http://localhost:3000"),
            ("127.0.0.1:3000", "https://", "http://127.0.0.1:3000"),
            ("[::1]:3000", "https://", "http://[::1]:3000"),
            ("app.localhost", "https://", "http://app.localhost"),
            ("app.example.com", "https://", "https://app.example.com"),
            ("192.168.1.10:3000", "http://", "http://192.168.1.10:3000"),
            # A scheme written into APP_URL wins.
            ("https://localhost:3443", "http://", "https://localhost:3443"),
            ("http://app.example.com", "https://", "http://app.example.com"),
            ("", "https://", ""),
        ],
    )
    def test_is_an_absolute_url(self, app_url, default_scheme, expected):
        scheme, host = _split_app_url(app_url)
        assert _app_base_url(host, scheme, default_scheme) == expected
