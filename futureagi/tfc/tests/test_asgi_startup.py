import runpy
from pathlib import Path
from types import SimpleNamespace

from tfc import asgi_startup


def test_warm_http_urlconf_materializes_every_nested_resolver(monkeypatch):
    class FakeResolver:
        def __init__(self, patterns):
            self._patterns = patterns
            self.read_count = 0

        @property
        def url_patterns(self):
            self.read_count += 1
            return self._patterns

    leaf = SimpleNamespace()
    nested = FakeResolver([leaf])
    root = FakeResolver([leaf, nested])
    monkeypatch.setattr(asgi_startup, "URLResolver", FakeResolver)
    monkeypatch.setattr(asgi_startup, "get_resolver", lambda: root)

    assert asgi_startup.warm_http_urlconf() == 3
    assert root.read_count == 1
    assert nested.read_count == 1


def test_wsgi_worker_warms_routes_after_application_setup(monkeypatch):
    """A WSGI worker pays route imports before it can expose its callable."""

    events = []
    application = object()
    monkeypatch.setattr(
        "tfc.telemetry.init_telemetry",
        lambda *, component: None,
    )
    monkeypatch.setattr(
        "django.core.wsgi.get_wsgi_application",
        lambda: events.append("application_ready") or application,
    )
    monkeypatch.setattr(
        asgi_startup,
        "warm_http_urlconf",
        lambda: events.append("urlconf_warm") or 3,
    )

    module = runpy.run_path(str(Path(__file__).parents[1] / "wsgi.py"))

    assert module["application"] is application
    assert events == ["application_ready", "urlconf_warm"]
